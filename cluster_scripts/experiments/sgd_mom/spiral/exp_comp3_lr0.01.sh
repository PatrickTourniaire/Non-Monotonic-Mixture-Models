python3 experiment_builder.py --experiment_name nmgmm_sgdmom_spiral_comp3_lr0.01 \
    --model squared_nm_gaussian_mixture \
    --data spiral \
    --comp 3 \
    --it 25000 \
    --lr 0.01 \
    --validate_pdf 0 \
    --optimizer sgd_mom \
    --initialisation random_from_data \
    --covar_shape full \
    --covar_reg 1