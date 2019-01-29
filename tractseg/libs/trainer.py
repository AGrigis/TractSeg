#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2017 Division of Medical Image Computing, German Cancer Research Center (DKFZ)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from os.path import join
import time
import pickle
import socket
import datetime
from tqdm import tqdm
import numpy as np

from tractseg.libs import exp_utils
from tractseg.libs import metric_utils
from tractseg.libs import dataset_utils
from tractseg.libs import plot_utils


def train_model(Config, model, data_loader):

    if Config.USE_VISLOGGER:
        try:
            from trixi.logger.visdom import PytorchVisdomLogger
        except ImportError:
            pass
        trixi = PytorchVisdomLogger(port=8080, auto_start=True)

    exp_utils.print_and_save(Config, socket.gethostname())

    epoch_times = []
    nr_of_updates = 0

    metrics = {}
    for type in ["train", "test", "validate"]:
        metrics_new = {
            "loss_" + type: [0],
            "f1_macro_" + type: [0],
        }
        metrics = dict(list(metrics.items()) + list(metrics_new.items()))

    loss_surface = []

    for epoch_nr in range(Config.NUM_EPOCHS):
        start_time = time.time()
        # current_lr = Config.LEARNING_RATE * (Config.LR_DECAY ** epoch_nr)
        # current_lr = Config.LEARNING_RATE

        batch_gen_time = 0
        data_preparation_time = 0
        network_time = 0
        metrics_time = 0
        saving_time = 0
        plotting_time = 0

        batch_nr = {
            "train": 0,
            "test": 0,
            "validate": 0
        }

        if Config.LOSS_WEIGHT_LEN == -1:
            weight_factor = float(Config.LOSS_WEIGHT)
        else:
            if epoch_nr < Config.LOSS_WEIGHT_LEN:
                weight_factor = -((Config.LOSS_WEIGHT-1) /
                                  float(Config.LOSS_WEIGHT_LEN)) * epoch_nr + float(Config.LOSS_WEIGHT)
            else:
                weight_factor = 1.

        #todo important: change
        # for type in ["train", "test", "validate"]:
        for type in ["validate"]:
            print_loss = []
            start_time_batch_gen = time.time()

            batch_gen = data_loader.get_batch_generator(batch_size=Config.BATCH_SIZE, type=type,
                                                        subjects=getattr(Config, type.upper() + "_SUBJECTS"))
            batch_gen_time = time.time() - start_time_batch_gen
            # print("batch_gen_time: {}s".format(batch_gen_time))

            if Config.DIM == "2D":
                nr_of_samples = len(getattr(Config, type.upper() + "_SUBJECTS")) * Config.INPUT_DIM[0]
            else:
                nr_of_samples = len(getattr(Config, type.upper() + "_SUBJECTS"))

            # *Config.EPOCH_MULTIPLIER needed to have roughly same number of updates/batches as with 2D U-Net
            # nr_batches = int(nr_of_samples / Config.BATCH_SIZE) * Config.EPOCH_MULTIPLIER
            nr_batches = 5

            print("Start looping batches...")
            start_time_batch_part = time.time()
            for i in range(nr_batches):
                batch = next(batch_gen)

                start_time_data_preparation = time.time()
                batch_nr[type] += 1

                x = batch["data"]  # (bs, nr_of_channels, x, y)
                y = batch["seg"]  # (bs, nr_of_classes, x, y)

                data_preparation_time += time.time() - start_time_data_preparation
                start_time_network = time.time()
                if type == "train":
                    nr_of_updates += 1
                    loss, probs, f1 = model.train(x, y, weight_factor=weight_factor)
                    # loss, probs, f1, intermediate = model.train(x, y)
                    print("TRAIN")

                elif type == "validate":

                    alpha = 0.001  # 0.005

                    if epoch_nr == 0:
                        loss, probs, f1 = model.test(x, y, weight_factor=weight_factor, alpha=alpha)
                        print("tmp loss: {}".format(loss))
                        loss_surface.append(loss)
                    elif epoch_nr == 1:
                        loss, probs, f1 = model.test(x, y, weight_factor=weight_factor, alpha=-alpha)
                        print("tmp loss: {}".format(loss))
                        loss_surface.insert(0, loss)   # append at beginning
                    else:
                        print("ERROR")

                elif type == "test":
                    loss, probs, f1 = model.test(x, y, weight_factor=weight_factor)
                    print("TEST")
                network_time += time.time() - start_time_network

                start_time_metrics = time.time()

                if Config.CALC_F1:
                    if Config.EXPERIMENT_TYPE == "peak_regression":
                        #Following two lines increase metrics_time by 30s (without < 1s);
                        #  time per batch increases by 1.5s by these lines
                        # y_flat = y.transpose(0, 2, 3, 1)  # (bs, x, y, nr_of_classes)
                        # y_flat = np.reshape(y_flat, (-1, y_flat.shape[-1]))  # (bs*x*y, nr_of_classes)
                        # metrics = metric_utils.calculate_metrics(metrics, y_flat, probs, loss, f1=np.mean(f1),
                        #                                          type=type, threshold=Config.THRESHOLD,
                        #                                          f1_per_bundle={"CA": f1[5], "FX_left": f1[23],
                        #                                                         "FX_right": f1[24]})

                        #Numpy
                        # y_right_order = y.transpose(0, 2, 3, 1)  # (bs, x, y, nr_of_classes)
                        # peak_f1 = metric_utils.calc_peak_dice(Config, probs, y_right_order)
                        # peak_f1_mean = np.array([s for s in peak_f1.values()]).mean()

                        # import IPython
                        # IPython.embed()

                        #Pytorch
                        peak_f1_mean = np.array([s.to('cpu') for s in list(f1.values())]).mean()  #if f1 for multiple bundles
                        metrics = metric_utils.calculate_metrics(metrics, None, None, loss, f1=peak_f1_mean, type=type,
                                                                 threshold=Config.THRESHOLD)

                        #Pytorch 2 F1
                        # peak_f1_mean_a = np.array([s for s in f1[0].values()]).mean()
                        # peak_f1_mean_b = np.array([s for s in f1[1].values()]).mean()
                        # metrics = metric_utils.calculate_metrics(metrics, None, None, loss, f1=peak_f1_mean_a,
                        #                                         type=type, threshold=Config.THRESHOLD,
                        #                                         f1_per_bundle={"LenF1": peak_f1_mean_b})

                        #Single Bundle
                        # metrics = metric_utils.calculate_metrics(metrics, None, None, loss, f1=f1["CST_right"][0],
                        #                                          type=type, threshold=Config.THRESHOLD,
                        #                                          f1_per_bundle={"Thr1": f1["CST_right"][1],
                        #                                                         "Thr2": f1["CST_right"][2]})
                        # metrics = metric_utils.calculate_metrics(metrics, None, None, loss, f1=f1["CST_right"],
                        #                                          type=type, threshold=Config.THRESHOLD)
                    else:
                        metrics = metric_utils.calculate_metrics(metrics, None, None, loss, f1=np.mean(f1), type=type,
                                                                 threshold=Config.THRESHOLD)

                else:
                    metrics = metric_utils.calculate_metrics_onlyLoss(metrics, loss, type=type)

                metrics_time += time.time() - start_time_metrics

                print_loss.append(loss)
                if batch_nr[type] % Config.PRINT_FREQ == 0:
                    time_batch_part = time.time() - start_time_batch_part
                    start_time_batch_part = time.time()
                    exp_utils.print_and_save(Config,
                                             "{} Ep {}, Sp {}, loss {}, t print {}s, "
                                             "t batch {}s".format(type, epoch_nr,
                                                                  batch_nr[type] * Config.BATCH_SIZE,
                                                                  round(np.array(print_loss).mean(), 6),
                                                                  round(time_batch_part, 3),
                                                                  round( time_batch_part / Config.PRINT_FREQ, 3)))
                    print_loss = []

                if Config.USE_VISLOGGER:
                    plot_utils.plot_result_trixi(trixi, x, y, probs, loss, f1, epoch_nr)


        ###################################
        # Post Training tasks (each epoch)
        ###################################

        #Adapt LR
        if Config.LR_SCHEDULE:
            model.scheduler.step()
            # model.scheduler.step(np.mean(f1))
            model.print_current_lr()

        # Average loss per batch over entire epoch
        # metrics = metric_utils.normalize_last_element(metrics, batch_nr["train"], type="train")
        # metrics = metric_utils.normalize_last_element(metrics, batch_nr["validate"], type="validate")
        # metrics = metric_utils.normalize_last_element(metrics, batch_nr["test"], type="test")
        #
        # print("  Epoch {}, Average Epoch loss = {}".format(epoch_nr, metrics["loss_train"][-1]))
        # print("  Epoch {}, nr_of_updates {}".format(epoch_nr, nr_of_updates))
        #
        # # Save Weights
        # start_time_saving = time.time()
        # if Config.SAVE_WEIGHTS:
        #     model.save_model(metrics, epoch_nr)
        # saving_time += time.time() - start_time_saving
        #
        # # Create Plots
        # start_time_plotting = time.time()
        # pickle.dump(metrics, open(join(Config.EXP_PATH, "metrics.pkl"), "wb"))
        # plot_utils.create_exp_plot(metrics, Config.EXP_PATH, Config.EXP_NAME)
        # plot_utils.create_exp_plot(metrics, Config.EXP_PATH, Config.EXP_NAME, without_first_epochs=True)
        # plotting_time += time.time() - start_time_plotting
        #
        # epoch_time = time.time() - start_time
        # epoch_times.append(epoch_time)
        #
        # exp_utils.print_and_save(Config, "  Epoch {}, time total {}s".format(epoch_nr, epoch_time))
        # exp_utils.print_and_save(Config, "  Epoch {}, time UNet: {}s".format(epoch_nr, network_time))
        # exp_utils.print_and_save(Config, "  Epoch {}, time metrics: {}s".format(epoch_nr, metrics_time))
        # exp_utils.print_and_save(Config, "  Epoch {}, time saving files: {}s".format(epoch_nr, saving_time))
        # exp_utils.print_and_save(Config, str(datetime.datetime.now()))
        #
        # # Adding next Epoch
        # if epoch_nr < Config.NUM_EPOCHS-1:
        #     metrics = metric_utils.add_empty_element(metrics)


    print("final loss surface:")
    print(loss_surface)


    ####################################
    # After all epochs
    ###################################
    with open(join(Config.EXP_PATH, "Hyperparameters.txt"), "a") as f:  # a for append
        f.write("\n\n")
        f.write("Average Epoch time: {}s".format(sum(epoch_times) / float(len(epoch_times))))

    return model


def predict_img(Config, model, data_loader, probs=False, scale_to_world_shape=True, only_prediction=False,
                batch_size=1):
    """
    Runtime on CPU
    - python 2 + pytorch 0.4:
      bs=1  -> 9min      around 4.5GB RAM (maybe even 7GB)
      bs=48 -> 6.5min           30GB RAM
    - python 3 + pytorch 1.0:
      bs=1  -> 2.7min    around 7GB RAM

    Args:
        Config:
        model:
        data_loader:
        probs:
        scale_to_world_shape:
        only_prediction:
        batch_size:

    Returns:

    """

    def finalize_data(layers):
        layers = np.array(layers)

        if Config.DIM == "2D":
            # Get in right order (x,y,z) and
            if Config.SLICE_DIRECTION == "x":
                layers = layers.transpose(0, 1, 2, 3)

            elif Config.SLICE_DIRECTION == "y":
                layers = layers.transpose(1, 0, 2, 3)

            elif Config.SLICE_DIRECTION == "z":
                layers = layers.transpose(1, 2, 0, 3)

        if scale_to_world_shape:
            layers = dataset_utils.scale_input_to_world_shape(layers, Config.DATASET, Config.RESOLUTION)

        return layers.astype(np.float32)

    #Test Time DAug
    for i in range(1):
        # segs = []
        # ys = []

        layers_seg = []
        layers_y = []
        batch_generator = data_loader.get_batch_generator(batch_size=batch_size)
        batch_generator = list(batch_generator)
        for batch in tqdm(batch_generator):
            x = batch["data"]   # (bs, nr_of_channels, x, y)
            y = batch["seg"]    # (bs, nr_of_classes, x, y)

            if not only_prediction:
                y = y.astype(Config.LABELS_TYPE)
                # y = np.squeeze(y)   # remove bs dimension which is only 1 -> (nrClasses, x, y)
                if Config.DIM == "2D":
                    y = y.transpose(0, 2, 3, 1) # (bs, x, y, nr_of_classes)
                else:
                    y = y.transpose(0, 2, 3, 4, 1)

            if Config.DROPOUT_SAMPLING:
                #For Dropout Sampling (must set Deterministic=False in model)
                NR_SAMPLING = 30
                samples = []
                for i in range(NR_SAMPLING):
                    layer_probs = model.predict(x)  # (bs, x, y, nrClasses)
                    samples.append(layer_probs)

                samples = np.array(samples)  # (NR_SAMPLING, bs, x, y, nrClasses)
                # samples = np.squeeze(samples) # (NR_SAMPLING, x, y, nrClasses)
                # layer_probs = np.mean(samples, axis=0)
                layer_probs = np.std(samples, axis=0)    # (bs,x,y,nrClasses)
            else:
                # For normal prediction
                layer_probs = model.predict(x)  # (bs, x, y, nrClasses)
                # layer_probs = np.squeeze(layer_probs)  # remove bs dimension which is only 1 -> (x, y, nrClasses)

            if probs:
                seg = layer_probs   # (x, y, nrClasses)
            else:
                seg = layer_probs
                seg[seg >= Config.THRESHOLD] = 1
                seg[seg < Config.THRESHOLD] = 0
                seg = seg.astype(np.int16)

            if Config.DIM == "2D":
                layers_seg.append(seg)
                if not only_prediction:
                    layers_y.append(y)
            else:
                layers_seg = seg
                if not only_prediction:
                    layers_y = y

    layers_seg = np.array(layers_seg)   # [i, bs, x, y, nr_classes]
    ls = layers_seg.shape
    layers_seg = np.reshape(layers_seg, (ls[0]*ls[1], ls[2], ls[3], ls[4]))  # [i*bs, x, y, nr_classes]
    layers_seg = finalize_data(layers_seg)

    if not only_prediction:
        layers_y = np.array(layers_y)  # [i, bs, x, y, nr_classes]
        ls = layers_y.shape
        layers_y = np.reshape(layers_y, (ls[0] * ls[1], ls[2], ls[3], ls[4]))  # [i*bs, x, y, nr_classes]
        layers_y = finalize_data(layers_y)

    return layers_seg, layers_y   # (Prediction, Groundtruth)
