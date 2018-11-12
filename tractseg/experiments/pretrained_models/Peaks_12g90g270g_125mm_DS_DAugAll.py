import os
from tractseg.experiments.peak_reg import Config as PeakRegConfig

#Name of original exp: Peaks20_12g90g270g_ALLoss

class Config(PeakRegConfig):
    EXP_NAME = os.path.basename(__file__).split(".")[0]

    LOSS_WEIGHT = 10
    LOSS_WEIGHT_LEN = 400  # nr of epochs

    NUM_EPOCHS = 250

    CLASSES = "All"     # All_Part1 / All_Part2 / All_Part3 / All_Part4
    MODEL = "UNet_Pytorch_DeepSup_Regression"

    DATA_AUGMENTATION = True
    DAUG_ELASTIC_DEFORM = True

    #todo important: change
    TRAINING_SLICE_DIRECTION = "xyz"