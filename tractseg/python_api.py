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

import warnings
warnings.simplefilter("ignore", UserWarning)    #hide scipy warnings
warnings.simplefilter("ignore", FutureWarning)    #hide h5py warnings
import importlib
import time
from os.path import join
import numpy as np

from tractseg.libs.system_config import SystemConfig as C
from tractseg.libs.system_config import get_config_name
from tractseg.libs import exp_utils
from tractseg.libs import utils
from tractseg.libs import dataset_utils
from tractseg.libs import direction_merger
from tractseg.libs import img_utils
from tractseg.data.data_loader_inference import DataLoaderInference
from tractseg.libs import trainer
from tractseg.models.base_model import BaseModel


def run_tractseg(data, output_type="tract_segmentation",
                 single_orientation=False, dropout_sampling=False, threshold=0.5,
                 bundle_specific_threshold=False, get_probs=False, peak_threshold=0.1,
                 postprocess=False, peak_regression_part="All", input_type="peaks",
                 nr_cpus=-1, verbose=False):
    """
    Run TractSeg

    Args:
        data: input peaks (4D numpy array with shape [x,y,z,9])
        output_type: TractSeg can segment not only bundles, but also the end regions of bundles.
            Moreover it can create Tract Orientation Maps (TOM).
            'tract_segmentation' [DEFAULT]: Segmentation of bundles (72 bundles).
            'endings_segmentation': Segmentation of bundle end regions (72 bundles).
            'TOM': Tract Orientation Maps (20 bundles).
        single_orientation: Do not run model 3 times along x/y/z orientation with subsequent mean fusion.
        dropout_sampling: Create uncertainty map by monte carlo dropout (https://arxiv.org/abs/1506.02142)
        threshold: Threshold for converting probability map to binary map
        bundle_specific_threshold: Set threshold to lower for some bundles which need more sensitivity (CA, CST, FX)
        get_probs: Output raw probability map instead of binary map
        peak_threshold: All peaks shorter than peak_threshold will be set to zero
        postprocess: Simple postprocessing of segmentations: Remove small blobs and fill holes
        peak_regression_part: Only relevant for output type 'TOM'. If set to 'All' (default) it will return all
            72 bundles. If set to 'Part1'-'Part4' it will only run for a subset of the bundles to reduce memory
            load.
        input_type: Always set to "peaks"
        nr_cpus: Number of CPUs to use. -1 means all available CPUs.
        verbose: Show debugging infos

    Returns:
        4D numpy array with the output of tractseg
        for tract_segmentation:     [x, y, z, nr_of_bundles]
        for endings_segmentation:   [x, y, z, 2*nr_of_bundles]
        for TOM:                    [x, y, z, 3*nr_of_bundles]
    """
    start_time = time.time()

    config = get_config_name(input_type, output_type, dropout_sampling=dropout_sampling)
    Config = getattr(importlib.import_module("tractseg.experiments.pretrained_models." + config), "Config")()
    Config.VERBOSE = verbose
    Config.TRAIN = False
    Config.TEST = False
    Config.SEGMENT = False
    Config.GET_PROBS = get_probs
    Config.LOAD_WEIGHTS = True
    Config.DROPOUT_SAMPLING = dropout_sampling
    Config.THRESHOLD = threshold
    Config.NR_CPUS = nr_cpus

    if bundle_specific_threshold:
        Config.GET_PROBS = True

    if input_type == "peaks":
        if Config.EXPERIMENT_TYPE == "tract_segmentation" and Config.DROPOUT_SAMPLING:
            Config.WEIGHTS_PATH = join(C.WEIGHTS_DIR, "pretrained_weights_tract_segmentation_dropout_v2.npz")
        elif Config.EXPERIMENT_TYPE == "tract_segmentation":
            Config.WEIGHTS_PATH = join(C.WEIGHTS_DIR, "pretrained_weights_tract_segmentation_v2.npz")
            # Config.WEIGHTS_PATH = join(C.NETWORK_DRIVE, "hcp_exp_nodes", "TractSeg_12g90g270g_125mm_DS_DAugAll_RotMirFlip", "best_weights_ep247.npz")
        elif Config.EXPERIMENT_TYPE == "endings_segmentation":
            Config.WEIGHTS_PATH = join(C.WEIGHTS_DIR, "pretrained_weights_endings_segmentation_v3.npz")
        elif Config.EXPERIMENT_TYPE == "dm_regression":
            Config.WEIGHTS_PATH = join(C.WEIGHTS_DIR, "pretrained_weights_dm_regression_v1.npz")
    elif input_type == "T1":
        if Config.EXPERIMENT_TYPE == "tract_segmentation":
            Config.WEIGHTS_PATH = join(C.NETWORK_DRIVE, "hcp_exp_nodes/x_Pretrained_TractSeg_Models",
                                       "TractSeg_T1_125mm_DAugAll", "best_weights_ep142.npz")
        elif Config.EXPERIMENT_TYPE == "endings_segmentation":
            Config.WEIGHTS_PATH = join(C.WEIGHTS_DIR, "pretrained_weights_endings_segmentation_v1.npz")
        elif Config.EXPERIMENT_TYPE == "peak_regression":
            # Config.WEIGHTS_PATH = join(C.WEIGHTS_DIR, "pretrained_weights_peak_regression_v1.npz")
            # Config.WEIGHTS_PATH = join(C.NETWORK_DRIVE, "hcp_exp_nodes",
            #                            "PeaksPart1_12g90g270g_125mm_DS_DAugAll_y", "best_weights_ep222.npz")
            Config.WEIGHTS_PATH = join(C.NETWORK_DRIVE, "hcp_exp_nodes",
                                       "PeaksPart1_12g90g270g_125mm_DS_DAugAll_xyz", "best_weights_ep140.npz")


    if Config.VERBOSE:
        print("Hyperparameters:")
        exp_utils.print_Configs(Config)

    data = np.nan_to_num(data)
    # brain_mask = img_utils.simple_brain_mask(data)
    # if Config.VERBOSE:
    #     nib.save(nib.Nifti1Image(brain_mask, np.eye(4)), "otsu_brain_mask_DEBUG.nii.gz")

    if input_type == "T1":
        data = np.reshape(data, (data.shape[0], data.shape[1], data.shape[2], 1))
    data, seg_None, bbox, original_shape = dataset_utils.crop_to_nonzero(data)
    data, transformation = dataset_utils.pad_and_scale_img_to_square_img(data, target_size=Config.INPUT_DIM[0])

    if Config.EXPERIMENT_TYPE == "tract_segmentation" or Config.EXPERIMENT_TYPE == "endings_segmentation" or \
            Config.EXPERIMENT_TYPE == "dm_regression":
        print("Loading weights from: {}".format(Config.WEIGHTS_PATH))
        Config.NR_OF_CLASSES = len(exp_utils.get_bundle_names(Config.CLASSES)[1:])
        utils.download_pretrained_weights(experiment_type=Config.EXPERIMENT_TYPE,
                                          dropout_sampling=Config.DROPOUT_SAMPLING)
        model = BaseModel(Config)
        if single_orientation:  # mainly needed for testing because of less RAM requirements
            data_loder_inference = DataLoaderInference(Config, data=data)
            if Config.DROPOUT_SAMPLING or Config.EXPERIMENT_TYPE == "dm_regression" or Config.GET_PROBS:
                seg, img_y = trainer.predict_img(Config, model, data_loder_inference, probs=True,
                                                 scale_to_world_shape=False, only_prediction=True)
            else:
                seg, img_y = trainer.predict_img(Config, model, data_loder_inference, probs=False,
                                                 scale_to_world_shape=False, only_prediction=True)
        else:
            seg_xyz, gt = direction_merger.get_seg_single_img_3_directions(Config, model, data=data,
                                                                           scale_to_world_shape=False,
                                                                           only_prediction=True)
            if Config.DROPOUT_SAMPLING or Config.EXPERIMENT_TYPE == "dm_regression" or Config.GET_PROBS:
                seg = direction_merger.mean_fusion(Config.THRESHOLD, seg_xyz, probs=True)
            else:
                seg = direction_merger.mean_fusion(Config.THRESHOLD, seg_xyz, probs=False)

    elif Config.EXPERIMENT_TYPE == "peak_regression":
        weights = {
            "Part1": "pretrained_weights_peak_regression_part1_v1.npz",
            "Part2": "pretrained_weights_peak_regression_part2_v1.npz",
            "Part3": "pretrained_weights_peak_regression_part3_v1.npz",
            "Part4": "pretrained_weights_peak_regression_part4_v1.npz",
        }
        if peak_regression_part == "All":
            parts = ["Part1", "Part2", "Part3", "Part4"]
            seg_all = np.zeros((data.shape[0], data.shape[1], data.shape[2], Config.NR_OF_CLASSES * 3))
        else:
            parts = [peak_regression_part]
            Config.CLASSES = "All_" + peak_regression_part
            Config.NR_OF_CLASSES = 3 * len(exp_utils.get_bundle_names(Config.CLASSES)[1:])

        for idx, part in enumerate(parts):
            Config.WEIGHTS_PATH = join(C.TRACT_SEG_HOME, weights[part])
            print("Loading weights from: {}".format(Config.WEIGHTS_PATH))
            Config.CLASSES = "All_" + part
            Config.NR_OF_CLASSES = 3 * len(exp_utils.get_bundle_names(Config.CLASSES)[1:])
            utils.download_pretrained_weights(experiment_type=Config.EXPERIMENT_TYPE,
                                              dropout_sampling=Config.DROPOUT_SAMPLING, part=part)
            data_loder_inference = DataLoaderInference(Config, data=data)
            model = BaseModel(Config)
            # seg, img_y = trainer.predict_img(Config, model, data_loder_inference, probs=True,
            #                                  scale_to_world_shape=False, only_prediction=True)

            seg_xyz, gt = direction_merger.get_seg_single_img_3_directions(Config, model, data=data,
                                                                           scale_to_world_shape=False,
                                                                           only_prediction=True)
            seg = direction_merger.mean_fusion(Config.THRESHOLD, seg_xyz, probs=True)


            if peak_regression_part == "All":
                seg_all[:, :, :, (idx*Config.NR_OF_CLASSES) : (idx*Config.NR_OF_CLASSES+Config.NR_OF_CLASSES)] = seg

        if peak_regression_part == "All":
            Config.CLASSES = "All"
            Config.NR_OF_CLASSES = 3 * len(exp_utils.get_bundle_names(Config.CLASSES)[1:])
            seg = seg_all

        #quite fast
        if bundle_specific_threshold:
            seg = img_utils.remove_small_peaks_bundle_specific(seg, exp_utils.get_bundle_names(Config.CLASSES)[1:],
                                                               len_thr=0.3)
        else:
            seg = img_utils.remove_small_peaks(seg, len_thr=peak_threshold)

    if bundle_specific_threshold and Config.EXPERIMENT_TYPE == "tract_segmentation":
        seg = img_utils.probs_to_binary_bundle_specific(seg, exp_utils.get_bundle_names(Config.CLASSES)[1:])

    #remove following two lines to keep super resolution
    seg = dataset_utils.cut_and_scale_img_back_to_original_img(seg, transformation)  # quite slow
    seg = dataset_utils.add_original_zero_padding_again(seg, bbox, original_shape, Config.NR_OF_CLASSES)  # quite slow

    if postprocess:
        seg = img_utils.postprocess_segmentations(seg, blob_thr=50, hole_closing=2)

    exp_utils.print_verbose(Config, "Took {}s".format(round(time.time() - start_time, 2)))
    return seg

