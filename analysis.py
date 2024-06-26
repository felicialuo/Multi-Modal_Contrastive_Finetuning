from models import VCLAPNet, TempNet
from data import ZS_LabeledVideoDataset

import torch
from tqdm import tqdm
import numpy as np
import argparse
import os

from sklearn.manifold import TSNE
from sklearn.metrics import ConfusionMatrixDisplay, confusion_matrix

import matplotlib.pyplot as plt

from transformers import VideoMAEImageProcessor, VideoMAEModel

import utils, data
from models import m2cf

def get_features_similarities_losses(model: VCLAPNet, loader: ZS_LabeledVideoDataset, classids: list[int], seen: bool = True, tensors_path: str = None):
    model.eval()
    
    if seen:
        model.enable_lora()
    else:
        model.disable_lora()

    all_preds, all_truths = [], []
    all_av_features = []
    all_similarities = []

    print("Generating Audio-Visual and Textual Features from VCLAPNet")

    with torch.no_grad():
        text_features = model.get_text_features().cpu()[classids]
        for batch in tqdm(loader, total=loader.dataset.num_videos//loader.batch_size+1, position=0, leave=False, desc="Features Generation"):
            logits_per_av, logits_per_text, av_features, _ = model(batch)
            preds = logits_per_av.argmax(dim=1)  # get the index of the max log-probability
            truths = batch["labels"].cpu()

            all_preds.append(preds.cpu())
            all_truths.append(truths)

            all_av_features.append(av_features.cpu())
            all_similarities.append(logits_per_av.cpu())
    
    all_preds = torch.cat(all_preds, dim=0)
    all_truths = torch.cat(all_truths, dim=0)
    all_av_features = torch.cat(all_av_features, dim=0)
    all_similarities = torch.cat(all_similarities, dim=0)

    if tensors_path:
        torch.save(
            obj = {
                "predictions": all_preds,
                "true labels": all_truths,
                "av features": all_av_features,
                "text features": text_features,
                "similarities": all_similarities
            }, 
            f = tensors_path
        )

    return all_truths, all_preds, all_av_features, all_similarities, text_features

def tsne_vis(input_features: torch.Tensor, text_features: torch.Tensor, labels: torch.Tensor, perplexities: tuple[int], id2colors: list[np.ndarray], 
             label2ids: dict[str, int], title: str, save_path: str | None = None):
    all_colors = id2colors[labels.numpy()]
    
    tsne_input_features = TSNE(perplexity=perplexities[0], random_state=0).fit_transform(input_features)
    tsne_text_features = TSNE(perplexity=perplexities[1], random_state=0).fit_transform(text_features)

    legend_elements, text_colors = [], []
    for C, i in label2ids.items():
        color = id2colors[i]
        legend_elements.append(plt.Line2D([0], [0], marker='s', color='w', label=C, markerfacecolor=color, markersize=10))
        text_colors.append(color)
    
    x, y = tsne_input_features.T
    plt.figure(figsize=(15, 15))
    plt.scatter(x, y, c=all_colors)
    plt.legend(handles=legend_elements, title="Classes", loc="lower left")
    plt.title(f"{title} -- Image Features")
    if save_path:
        plt.savefig(os.path.join(save_path, "tsne-av-feats.png"))
    plt.show()

    x, y = tsne_text_features.T
    plt.figure(figsize=(15, 15))
    plt.scatter(x, y, c=text_colors, s=100)
    plt.legend(handles=legend_elements, title="Classes", loc="lower left")
    plt.title(f"{title} -- Text Features")
    if save_path:
        plt.savefig(os.path.join(save_path, "tsne-text-feats.png"))
    plt.show()

def plot_confusion_matrix(y_true, y_pred, label2ids: dict[str, int], all_classes: list[str], fsize=(15, 15), save_path: str | None = None):
    _, ax = plt.subplots(figsize=fsize)
    # ConfusionMatrixDisplay.from_predictions(y_true, y_pred, ax=ax, labels=np.arange(len(labels)), display_labels=labels,
    #                                          xticks_rotation="vertical", normalize="true")
    classes = list(label2ids.keys())
    classids = list(label2ids.values())

    cm = confusion_matrix(y_true, y_pred, labels=np.arange(len(all_classes)))[classids].astype(np.float32)
    cm /= np.sum(cm, axis=1, keepdims=True)

    # make sure to swap classes present in dataset to the first columns
    new_cm = np.zeros_like(cm, dtype=np.float32)
    new_classes = [None for _ in range(len(all_classes))]
    n_col_ids = [0, len(classes)]
    for i, C in enumerate(all_classes):
        col = cm[:, i]

        idx = 0 if C in classes else 1
        new_cm[:, n_col_ids[idx]] = col
        new_classes[n_col_ids[idx]] = C      # make xticks correct
        n_col_ids[idx] += 1
    cm = new_cm

    plt.imshow(cm, interpolation='nearest', cmap=plt.cm.Blues)
    plt.title('Confusion Matrix')
    plt.colorbar()
    tick_marks = np.arange(len(all_classes))
    plt.xticks(tick_marks, new_classes, rotation=90)
    plt.yticks(tick_marks[:len(classes)], classes)

    thresh = cm.max() / 2.
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            n = cm[i, j]
            plt.text(j, i, f"{int(n):d}" if n.is_integer() else f"{n:.2f}",
                    ha="center", va="center",
                    color="white" if cm[i, j] > thresh else "black")

    # plt.tight_layout()
    plt.ylabel('True label')
    plt.xlabel('Predicted label')
    plt.show()

    if save_path:
        plt.savefig(os.path.join(save_path, "confusion_matrix.png"))
    plt.show()
    
    
def get_sim_recall(labels: torch.Tensor, preds: torch.Tensor, similarity: torch.Tensor, label2ids: dict[str, int], id2labels: dict[int, str]):
    classes = list(label2ids.keys())
    sims = {
        c: 0. for c in classes
    }

    correct_nums = {
        c: 0 for c in classes
    }

    ttl_nums = {
        c: 0 for c in classes
    }

    similarity = similarity.softmax(dim=-1)

    for i, (label, pred) in enumerate(zip(labels, preds)):
        C = id2labels[label.item()]
        ttl_nums[C] += 1
        correct_nums[C] += int(label.item() == pred.item())

        sims[C] += similarity[i][label]
    
    for k in classes:
        if ttl_nums[k] == 0:
            print(f"Warning: {k} has no data!")
            continue

        sims[k] /= ttl_nums[k]
        correct_nums[k] /= ttl_nums[k]
    
    return sims, correct_nums

def sim_recall_plot(sims: dict[str, float], recalls: dict[str, float], save_path: str | None = None):
    heights = [sims[c] for c in sims.keys()]
    y = [recalls[c] for c in sims.keys()]
    x = np.arange(len(heights))
    fig, ax1 = plt.subplots(figsize=(25, 8))
    ax1.bar(sims.keys(), height=heights, color="tab:red")
    ax1.set_xlabel("Class")
    ax1.set_ylabel("Average Similarity", color="tab:red")
    ax1.tick_params(axis='x', labelrotation=90)
    ax1.tick_params(axis='y', labelcolor="tab:red")

    ax2 = plt.twinx(ax1)
    ax2.plot(x, y, marker='o', label="Recall", color="tab:blue")
    ax2.set_xlabel("Class")
    ax2.set_ylabel("Recall", color="tab:blue")
    ax2.tick_params(axis='y', labelcolor="tab:blue")

    plt.title("Similarity & Recall vs. Class")
    plt.legend()
    plt.tight_layout()
    if save_path:
        plt.savefig(os.path.join(save_path, "sim_recall.png"))
    plt.show()


def arg_parse():
    parser = argparse.ArgumentParser(
        description="Multi-Modal Machine Learning Project, Intrinsic Metrics Analysis"
    )

    parser.add_argument("--all", action="store_true", help="whether to run all analysis")
    parser.add_argument("--tsne", action="store_true", help="whether to plot t-SNE distributions")
    parser.add_argument("--confusion_matrix", action="store_true", help="whether to plot confusion matrix")
    parser.add_argument("--recall_similarity", action="store_true", help="whether to plot per-class recall and similarity")
    parser.add_argument("--auto_save", action="store_true", help="whether to automatically save the plots")
    parser.add_argument("--save_tensors", action="store_true", help="whether to save the generated features")
    parser.add_argument("--use_gpu", action="store_true", help="whether to use GPU for inference")
    parser.add_argument("--resume", action="store_true", help="wheter to resume from previously generated features")

    parser.add_argument("--dataset", type=str, default="seen",
                      choices=["seen", "unseen"], help="the dataset to run analysis on")
    parser.add_argument("--plot_path", type=str, default="./plots/", help="directory path to save plots, only useful if auto_save")
    parser.add_argument("--tensors_path", type=str, default="./tensors/", help="directory path to save the generated features")
    parser.add_argument("--audiomae_ckpt", type=str, default="D:/Models/AudioMAE/pretrained.pth", help="checkpoint of AudioMAE model, only if using m2cf-v3")
    parser.add_argument("--model_ckpt", type=str, default="D:/Models/CLIP/epoch5.pt", help="checkpoint of the whole model")
    parser.add_argument("--data_path", type=str, default="D:/DATA/UCF101/UCF-101-SEEN/", help="path to the UCF-101 dataset")

    parser.add_argument("--m2cf_ver", type=int, default=2, choices=[2, 3], help="version of m2cf model to run analysis, only support v2 or v3")
    parser.add_argument("--batch_size", type=int, default=4, help="batch size used for the data loader")
    parser.add_argument("--perplexities", type=int, nargs=2, default=[30, 30], help="perplexities used for t-SNE visulaization")

    return parser.parse_args()

if __name__ == "__main__":
    args = arg_parse()
    utils.arg_print(args)

    device = torch.device("cuda" if args.use_gpu and torch.cuda.is_available() else "cpu")

    print("\nLoading the VideoMAE model and image processor......")
    image_processor = VideoMAEImageProcessor.from_pretrained("MCG-NJU/videomae-base")
    videomae_model = VideoMAEModel.from_pretrained("MCG-NJU/videomae-base")

    print("\nCreating the dataset and dataloader......")
    train_dataset, test_seen_dataset, test_unseen_dataset = data.get_iterable_dataset("ucf", args.data_path, image_processor,
                                                                        num_frames_to_sample=16, sample_rate=8, fps=30)        
    _, test_seen_loader, test_unseen_loader = data.get_iterable_dataloader(train_dataset, test_seen_dataset, test_unseen_dataset, 
                                                                        image_processor, data.collate_fn, videomae_model, batch_size=args.batch_size)
    
    tup = utils.get_sep_seen_unseen_labels(args.data_path)
    if args.dataset == "seen":
        dataloader = test_seen_loader
        label2ids = tup[0]
        id2labels = tup[1]
        seen = True
    else:
        dataloader = test_unseen_loader
        label2ids = tup[2]
        id2labels = tup[3]
        seen = False
    
    print(f"\nLoading the M2CF Version {args.m2cf_ver} model......")
    all_classnames = list(utils.get_labels()[0].keys())
    model = m2cf(classnames=all_classnames, audiomae_ckpt=args.audiomae_ckpt, model_ckpt=args.model_ckpt, version=args.m2cf_ver, device=device)

    tensors_path = None
    if args.save_tensors or args.resume:
        # should have a tensors path
        tensors_path = os.path.join(args.tensors_path, args.dataset, f"V{args.m2cf_ver}")
        if args.save_tensors and not os.path.exists(tensors_path):
            # when we wantto save tensors, we might need to create folder
            os.makedirs(tensors_path)
        tensors_path = os.path.join(tensors_path, "tensors.pt")
    
    if args.resume:
        print("Resume from previously generated predictions, features and similarities, loading......")
        t = torch.load(tensors_path)
        preds = t["predictions"]
        truths = t["true labels"]
        av_features = t["av features"]
        text_features = t["text features"]
        similarities = t["similarities"]
    else:
        print("\nGenerating all the Audio-Visual and Textual embeddings and calculating similarities......")
        classids = list(label2ids.values())
        truths, preds, av_features, similarities, text_features = get_features_similarities_losses(model, dataloader, classids=classids, seen=seen, tensors_path=tensors_path)
    print(f"Accuracy: {torch.sum(truths == preds) / truths.shape[0] * 100:2f}%")

    # make sure the save path is correct
    save_path = None
    if args.auto_save:
        save_path = os.path.join(args.plot_path, args.dataset, f"V{args.m2cf_ver}")
        if not os.path.exists(save_path):
            os.makedirs(save_path)

    if args.all or args.tsne:
        print("\nPlotting t-SNE distributions for the generated features......")
        reds, greens, blues = np.linspace(0, 1, 51), np.linspace(0, 1, 51), np.linspace(0, 1, 51)
        np.random.seed(0)
        np.random.shuffle(reds)
        np.random.shuffle(greens)
        np.random.shuffle(blues)

        reds = reds.reshape((-1, 1))
        greens = greens.reshape((-1, 1))
        blues = blues.reshape((-1, 1))

        class_colors = np.concatenate((reds, greens, blues), axis=1)
        assert(class_colors.shape[0] == 51 and class_colors.shape[1] == 3)

        tsne_vis(av_features, text_features, truths, perplexities=args.perplexities, id2colors=class_colors, label2ids=label2ids, 
                 title=f"Test {args.dataset.capitalize()} Dataset", save_path=save_path)
    
    if args.all or args.confusion_matrix:
        print("\nPlotting the confusion matrix......")
        plot_confusion_matrix(truths, preds, label2ids, all_classnames, fsize=(30, 8) if args.dataset == "unseen" else (30, 21), save_path=save_path)
    
    if args.all or args.recall_similarity:
        print("\nPlotting graph for per-class recall and similarities distributions......")
        sims, recalls = get_sim_recall(truths, preds, similarities, label2ids, id2labels)
        sim_recall_plot(sims, recalls, save_path=save_path)