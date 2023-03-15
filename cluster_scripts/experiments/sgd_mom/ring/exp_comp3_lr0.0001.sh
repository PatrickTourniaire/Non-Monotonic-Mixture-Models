python3 experiment_builder.py --experiment_name nmgmm_sgdmom_ring_comp3_lr0.0001 \
    --model squared_nm_gaussian_mixture \
    --data ring \
    --comp 3 \
    --it 25000 \
    --lr 0.0001 \
    --validate_pdf 0 \
    --optimizer sgd_mom \
    --initialisation random \
    --covar_shape diag \
    --covar_reg 0