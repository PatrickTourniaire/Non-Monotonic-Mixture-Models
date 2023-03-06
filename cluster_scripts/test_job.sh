#!/bin/bash

# Grid Engine options (lines prefixed with #$)
# Runtime limit of 1 hour:
#$ -l h_rt=01:00:00
#
# Set working directory to the directory where the job is submitted from:
#$ -cwd
#
# Request one GPU: 
#$ -pe gpu 1
#
# Request 4 GB system RAM 
# the total system RAM available to the job is the value specified here multiplied by 
# the number of requested GPUs (above)
#$ -l h_vmem=8G

# Initialise the environment modules and load CUDA version 8.0.61
. /etc/profile.d/modules.sh
module load anaconda/2022.05

conda activate nmmm

# Run the program
./local_scripts/squared_gaussian_nm_mixture_test.sh
