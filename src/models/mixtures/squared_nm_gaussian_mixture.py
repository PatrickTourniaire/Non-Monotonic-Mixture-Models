# External imports
import numpy as np
import torch
import torch.nn as nn
import torch.optim
from matplotlib import pyplot as plt
from torch import linalg
from scipy import integrate

# Local imports
from .hooks import HookTensorBoard, BaseHookVisualise


# Equations
cholesky_comp = lambda L, D: L @ L.t() + torch.eye(D)
mahalanobis   = lambda x, mu, S_inv: (x - mu).t() @ S_inv @ (x - mu)


def _batch_mahalanobis(bL, bx):
    """
    Computes the squared Mahalanobis distance :math:`\mathbf{x}^\top\mathbf{M}^{-1}\mathbf{x}`
    for a factored :math:`\mathbf{M} = \mathbf{L}\mathbf{L}^\top`.

    Accepts batches for both bL and bx. They are not necessarily assumed to have the same batch
    shape, but `bL` one should be able to broadcasted to `bx` one.
    """
    n = bx.size(-1)
    bx_batch_shape = bx.shape[:-1]

    # Assume that bL.shape = (i, 1, n, n), bx.shape = (..., i, j, n),
    # we are going to make bx have shape (..., 1, j,  i, 1, n) to apply batched tri.solve
    bx_batch_dims = len(bx_batch_shape)
    bL_batch_dims = bL.dim() - 2
    outer_batch_dims = bx_batch_dims - bL_batch_dims
    old_batch_dims = outer_batch_dims + bL_batch_dims
    new_batch_dims = outer_batch_dims + 2 * bL_batch_dims
    # Reshape bx with the shape (..., 1, i, j, 1, n)
    bx_new_shape = bx.shape[:outer_batch_dims]
    for (sL, sx) in zip(bL.shape[:-2], bx.shape[outer_batch_dims:-1]):
        bx_new_shape += (sx // sL, sL)
    bx_new_shape += (n,)
    bx = bx.reshape(bx_new_shape)
    # Permute bx to make it have shape (..., 1, j, i, 1, n)
    permute_dims = (list(range(outer_batch_dims)) +
                    list(range(outer_batch_dims, new_batch_dims, 2)) +
                    list(range(outer_batch_dims + 1, new_batch_dims, 2)) +
                    [new_batch_dims])
    bx = bx.permute(permute_dims)

    flat_L = bL.reshape(-1, n, n)  # shape = b x n x n
    flat_x = bx.reshape(-1, flat_L.size(0), n)  # shape = c x b x n
    flat_x_swap = flat_x.permute(1, 2, 0)  # shape = b x n x c
    M_swap = torch.linalg.solve_triangular(flat_L, flat_x_swap, upper=False).pow(2).sum(-2)  # shape = b x c
    M = M_swap.t()  # shape = c x b

    # Now we revert the above reshape and permute operators.
    permuted_M = M.reshape(bx.shape[:-1])  # shape = (..., 1, j, i, 1)
    permute_inv_dims = list(range(outer_batch_dims))
    for i in range(bL_batch_dims):
        permute_inv_dims += [outer_batch_dims + i, old_batch_dims + i]
    reshaped_M = permuted_M.permute(permute_inv_dims)  # shape = (..., 1, i, j, 1)
    return reshaped_M.reshape(bx_batch_shape)


class NMSquaredGaussianMixture(nn.Module, HookTensorBoard, BaseHookVisualise):

    def __init__(self, n_clusters: int, n_dims: int, device: str):
        super(NMSquaredGaussianMixture, self).__init__()
        self.device = device
        
        # Configurations
        self.n_dims = n_dims
        self.n_clusters = n_clusters

        # Parameters of component means (n_clusters, n_dim)
        self.means = nn.Parameter(torch.zeros(n_clusters, n_dims, dtype=torch.float64))
        # Parameters of matrices used for Cholesky composition (n_clusters, n_dim, n_dim)
        self.chols = nn.Parameter(torch.zeros(n_clusters, n_dims, n_dims, dtype=torch.float64).normal_())
        # Parameter for the weights of each mixture component (n_clusters,)
        self.weights = nn.Parameter(torch.rand(n_clusters, dtype=torch.float64).normal_(-1,1))

        # Parameters used for tensorboard
        self.tb_params = {}
    

    #===================================================================================================
    #                                       INTERNAL COMPUTATIONS
    #===================================================================================================

    def _chol_composition(self) -> torch.Tensor:
        self.sigmas = [cholesky_comp(self.chols[i], self.n_dims) for i in range(self.n_clusters)]
    

    def _sqrd_params(self, i: int, j: int) -> torch.Tensor:
        sigma_i, sigma_j = torch.inverse(self.sigmas[i]), torch.inverse(self.sigmas[j])
        mu_i, mu_j = self.means[i], self.means[j]

        # Compute the squared Gaussian params
        sigma = torch.inverse(sigma_i + sigma_j)
        mu = sigma @ (sigma_i @ mu_i + sigma_j @ mu_j)

        return (sigma, mu)

    def _squared_norm_term(self, i: int, j: int):
        sigma_i, sigma_j = self.sigmas[i], self.sigmas[j]
        mu_i, mu_j = self.means[i], self.means[j]

        z_term = 1 / torch.sqrt(np.power(2 * np.pi, self.n_dims) * torch.det(sigma_i + sigma_j))
        mahalanobis_dist = _batch_mahalanobis(torch.linalg.cholesky(sigma_i + sigma_j), (mu_i - mu_j))
        #mahalanobis_dist = torch.matmul(torch.matmul((mu_i - mu_j).t(), torch.inverse(sigma_i + sigma_j)), (mu_i - mu_j))


        return z_term * torch.exp(- .5 * mahalanobis_dist)
    

    def _samplerange(self, samples: torch.Tensor):
        x, y = samples[:,0], samples[:,1]

        return (x.min(), x.max(), y.min(), y.max())


    def _validation(self, samples: torch.Tensor, it: int):
        x_pad, y_pad = 10, 10
        x_min, x_max, y_min, y_max = self._samplerange(samples)

        f = lambda x, y: self.pdf(torch.Tensor([[x, y]])).data.cpu().numpy().astype(float)[0]
        integral, _ = integrate.dblquad(f, x_min-x_pad, x_max+x_pad, y_min-y_pad, y_max+y_pad)

        self.add_integral(torch.Tensor([integral]), it)


    #===================================================================================================
    #                                        LIKELIHOOD METHODS
    #===================================================================================================

    def pdf(self, X: torch.Tensor) -> torch.Tensor:
        self._chol_composition()

        # Lambda expressions for calculating the PDF
        norm_constant    = lambda S: torch.sqrt(np.power(2 * np.pi, self.n_dims) * torch.det(S))
        exponential      = lambda L, mu: - .5 * _batch_mahalanobis(L, (X - mu))
        density_function = lambda S, L, mu: (1 / norm_constant(S)) * torch.exp(exponential(L, mu))

        cluster_ids = torch.Tensor(range(self.n_clusters))
        cartesian_ids = torch.cartesian_prod(cluster_ids, cluster_ids)
        cartesian_ids = cartesian_ids.data.cpu().numpy().astype(int)

        component_likelihoods = []
        normalisers = []
        
        tb_means = []
        tb_sigmas = []
        tb_weights = []

        for (i, j) in cartesian_ids:
            sigma, means = self._sqrd_params(i, j)
            weight = self.weights[i] * self.weights[j]

            l_a = density_function(self.sigmas[i], torch.linalg.cholesky(self.sigmas[i]), self.means[i])
            l_b = density_function(self.sigmas[j], torch.linalg.cholesky(self.sigmas[j]), self.means[j])
            likelihood = l_a * l_b
            
            component_likelihoods.append(weight * likelihood)
            normalisers.append(self._squared_norm_term(i, j) * weight)

            tb_weights.append(weight)
            tb_means.append(means)
            tb_sigmas.append(sigma)
        
        self.tb_params['means'] = torch.stack(tb_means, dim=0)
        self.tb_params['sigmas'] = torch.stack(tb_sigmas, dim=0)
        self.tb_params['weights'] = torch.stack(tb_weights, dim=0)

        z = torch.stack(normalisers, dim=0).sum(dim=0)
        likelihoods = 1/z * torch.stack(component_likelihoods, dim=0).sum(dim=0)

        return likelihoods

    def log_likelihoods(self, X: torch.Tensor) -> torch.Tensor:
        return torch.log(self.pdf(X))
    

    def neglog_likelihood(self, X: torch.Tensor) -> torch.Tensor:
        return - (self.log_likelihoods(X).logsumexp(dim=0) / X.shape[0])


    def forward(self, X: torch.Tensor, it: int, validate: bool = False) -> torch.Tensor:
        if validate and (it % 100 == 0): self._validation(X, it)
        out = self.neglog_likelihood(X)

        if not self.monitor: return out
        
        self.add_means(self.tb_params['means'] , it)
        self.add_weights(self.tb_params['weights'], it)
        self.add_loss(out, it)

        return out


    def val_loss(self, X: torch.Tensor, it: int) -> torch.Tensor:
        out = self.neglog_likelihood(X)
        self.add_valloss(out, it)

        return out
    

    #===================================================================================================
    #                                       VISUALISATION METHODS
    #===================================================================================================

    def plot_contours(self, samples: torch.Tensor, save_to: str):
        _, ax = plt.subplots()
        x, y = samples[:,0], samples[:,1]
        ax.scatter(x, y, s=0.5)

        ax.axvline(c='grey', lw=1)
        ax.axhline(c='grey', lw=1)

        for i in range(len(self.tb_params['means'])):
            sigma = self.tb_params['sigmas'][i].data.cpu().numpy()
            mu = self.tb_params['means'][i].data.cpu().numpy()
            
            colour = 'blue' if self.tb_params['weights'][i] > 0 else 'red'
            config = {
                'edgecolor': colour,
                'facecolor': colour,
                'alpha': .2
            }

            self._confidence_ellipse(ax, sigma, mu, **config)
            ax.scatter(mu[0], mu[1], c='red', s=3)
        
        ax.set_title('Non-Monotonic Gaussian Mixture')
        plt.savefig(save_to)
    

    def plot_heatmap(
            self, 
            train_samples: torch.Tensor,
            val_samples: torch.Tensor,
            save_to: str):
        idx_i, idx_j = 0,1

        ngrid = 100
        eval_grid = np.linspace(-8,8,ngrid)

        cond_values = np.zeros(2)
        epsilon = 1.5

        eval_points = self.get_grid(eval_grid, idx_i, idx_j, cond_values)


        logpdf = self.pdf(torch.from_numpy(eval_points)).data.cpu().numpy()

        fig, ax = plt.subplots(1,1, figsize=(6,6), sharex=True, sharey=True)

        ax.set_xlim([-8, 8])
        ax.set_ylim([-8, 8])
        plt.axis('square')

        c = ax.pcolor(eval_grid, eval_grid, logpdf.reshape(ngrid, ngrid), vmin=0)
        ax.scatter(train_samples[:,idx_i], train_samples[:,idx_j], 1, color="r", alpha=0.5)
        ax.scatter(val_samples[:,idx_i], val_samples[:,idx_j], 1, color="k", alpha=0.5)
        
        fig.colorbar(c, ax=ax)

        plt.savefig(save_to)
