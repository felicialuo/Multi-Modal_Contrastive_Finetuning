import torch
import torch.nn.functional as F
from utils import euclidean_distance

def symmetric_ce(logits_av, logits_text, gt, num_classes: int = 51):
    loss_av = F.cross_entropy(logits_av, gt)
    gt_text = F.one_hot(gt, num_classes=num_classes).T.float()
    # logits_text = F.softmax(logits_text, dim=0)
    loss_text = F.binary_cross_entropy_with_logits(logits_text, gt_text)
    return (loss_av + loss_text) / 2

def composite_loss(logits_av, logits_text, av_features, text_features, gt):
    text_features = text_features[gt]
    # mse = F.mse_loss(av_features, text_features)
    device = text_features.device
    cossim_loss = F.cosine_embedding_loss(av_features, text_features, torch.ones(av_features.shape[0]).to(device))
    sym_ce = symmetric_ce(logits_av, logits_text, gt)
    # return mse + sym_ce
    return cossim_loss + sym_ce

def euclidean_distance_loss(av_features: torch.Tensor, text_features: torch.Tensor, gt: torch.Tensor, lam: float = 1.0, eta: float = 10.0,
                       num_classes: int = 51):
    euc_dist = euclidean_distance(av_features, text_features)

    # mask = torch.ones_like(euc_dist)
    # mask = mask.scatter(dim=1, index=gt[None], src=torch.zeros_like(euc_dist))
    # mask = mask.bool()

    # loss1 = (euc_dist[mask] * -lam).sum()
    # loss2 = (euc_dist[~mask] * eta).sum()

    # return (loss1 + loss2) / euc_dist.numel()
    return symmetric_ce(euc_dist, euc_dist.t(), gt, num_classes)