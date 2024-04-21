import torch
import torch.nn.functional as F

def symmetric_ce(logits_av, logits_text, gt):
    loss_av = F.cross_entropy(logits_av, gt)
    gt_text = F.one_hot(gt, num_classes=400).T.float()
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