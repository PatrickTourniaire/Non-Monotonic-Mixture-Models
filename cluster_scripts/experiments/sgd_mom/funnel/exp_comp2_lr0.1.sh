python3 experiment_builder.py --experiment_name nmgmm_sgdmom_funnel_comp2_lr0.1 \
    --model squared_nm_gaussian_mixture \
    --data funnel \
    --comp 2 \
    --it 25000 \
    --lr 0.1 \
    --validate_pdf 0 \
    --optimizer sgd_mom \
    --initialisation random_from_data \
    --covar_shape full \
    --covar_reg 1