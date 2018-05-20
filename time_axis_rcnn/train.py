#!/usr/local/anaconda3/bin/python3
from __future__ import division

import sys

from time_axis_rcnn.constants.enum_type import OptimizerType
from time_axis_rcnn.datasets.npz_feature_dataset import NpzFeatureDataset
from time_axis_rcnn.model.time_segment_network.faster_head_module import FasterHeadModule
from time_axis_rcnn.model.time_segment_network.faster_rcnn_backbone import FasterBackbone
from time_axis_rcnn.model.time_segment_network.faster_rcnn_train_chain import TimeSegmentRCNNTrainChain
from time_axis_rcnn.model.time_segment_network.segment_proposal_network import SegmentProposalNetwork

sys.path.insert(0, '/home/machen/face_expr')


try:
    import matplotlib
    matplotlib.use('agg')
except ImportError:
    pass

import argparse
import numpy as np
import os

import chainer
from chainer import training

from chainer.datasets import TransformDataset
from chainer.dataset import concat_examples
from dataset_toolkit.adaptive_AU_config import adaptive_AU_database
import config
from chainer.iterators import MultiprocessIterator, SerialIterator
from dataset_toolkit.squeeze_label_num_report import squeeze_label_num_report


def main():
    parser = argparse.ArgumentParser(
        description='train script of Time-axis R-CNN:')
    parser.add_argument('--pid', '-pp', default='/tmp/SpaceTime_AU_R_CNN/')
    parser.add_argument('--gpu', '-g', nargs='+', type=int, help='GPU ID, multiple GPU split by space')
    parser.add_argument('--lr', '-l', type=float, default=0.001)
    parser.add_argument('--out', '-o', default='end_to_end_result',
                        help='Output directory')
    parser.add_argument('--database',  default='BP4D',
                        help='Output directory: BP4D/DISFA/BP4D_DISFA')
    parser.add_argument('--iteration', '-i', type=int, default=70000)
    parser.add_argument('--optimizer', type=OptimizerType,choices=list(OptimizerType))
    parser.add_argument('--epoch', '-e', type=int, default=20)
    parser.add_argument('--batch_size', '-bs', type=int, default=1)
    parser.add_argument('--feature_dim', type=int, default=2048)
    parser.add_argument('--roi_size', type=int, default=10)
    parser.add_argument('--snapshot', '-snap', type=int, default=1000)
    parser.add_argument("--fold", '-fd', type=int, default=3)
    parser.add_argument("--data_dir", type=str, default="/extract_features")
    parser.add_argument("--conv_layers", type=int, default=5)
    parser.add_argument("--split_idx",'-sp', type=int, default=1)
    parser.add_argument("--use_paper_num_label", action="store_true", help="only to use paper reported number of labels"
                                                                           " to train")
    parser.add_argument("--debug", action="store_true", help="debug mode for 1/50 dataset")
    parser.add_argument("--snap_individual", action="store_true", help="whether to snapshot each individual epoch/iteration")

    parser.add_argument("--proc_num", "-proc", type=int, default=1)
    args = parser.parse_args()
    args.data_dir = config.ROOT_PATH + "/" + args.data_dir
    os.makedirs(args.pid, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)
    pid = str(os.getpid())
    pid_file_path = args.pid + os.path.sep + "{0}_{1}_fold_{2}.pid".format(args.database, args.fold, args.split_idx)
    with open(pid_file_path, "w") as file_obj:
        file_obj.write(pid)
        file_obj.flush()


    print('GPU: {}'.format(",".join(list(map(str, args.gpu)))))

    adaptive_AU_database(args.database)

    paper_report_label, class_num = squeeze_label_num_report(args.database, args.use_paper_num_label)
    paper_report_label_idx = list(paper_report_label.keys())

    faster_extractor_backbone = FasterBackbone(args.database, args.conv_layers, args.feature_dim, 1024)
    faster_head_module = FasterHeadModule(1024, class_num + 1, args.roi_size)  # note that the class number here must include background
    initialW = chainer.initializers.Normal(0.001)
    spn = SegmentProposalNetwork(args.database, 1024, n_anchors=len(config.ANCHOR_SIZE), initialW=initialW)

    model = TimeSegmentRCNNTrainChain(faster_extractor_backbone, faster_head_module, spn)
    if args.gpu >= 0:
        model.to_gpu(args.gpu)
        chainer.cuda.get_device(args.gpu).use()
    optimizer = None
    if args.optimizer == OptimizerType.AdaGrad:
        optimizer = chainer.optimizers.AdaGrad(
            lr=args.lr)  # 原本为MomentumSGD(lr=args.lr, momentum=0.9) 由于loss变为nan问题，改为AdaGrad
    elif args.optimizer == OptimizerType.RMSprop:
        optimizer = chainer.optimizers.RMSprop(lr=args.lr)
    elif args.optimizer == OptimizerType.Adam:
        optimizer = chainer.optimizers.Adam(alpha=args.lr)
    elif args.optimizer == OptimizerType.SGD:
        optimizer = chainer.optimizers.MomentumSGD(lr=args.lr, momentum=0.9)
    elif args.optimizer == OptimizerType.AdaDelta:
        optimizer = chainer.optimizers.AdaDelta()

    optimizer.setup(model)
    optimizer.add_hook(chainer.optimizer.WeightDecay(rate=0.0005))
    data_dir = args.data_dir + "{0}_{1}_fold_{2}/train".format(args.database, args.fold, args.split_idx)
    dataset = NpzFeatureDataset(data_dir, args.database, paper_report_label_idx=paper_report_label_idx)
    if args.proc_num == 1:
        train_iter = SerialIterator(dataset, args.batch_size, repeat=True, shuffle=True)
    else:
        train_iter = MultiprocessIterator(dataset,  batch_size=args.batch_size,
                                          n_processes=args.proc_num,
                                      repeat=True, shuffle=True, n_prefetch=10, shared_mem=10000000)
    chainer.cuda.get_device_from_id(args.gpu[0]).use()
    model.to_gpu(args.gpu[0])

    # BP4D_3_fold_1_resnet101@rnn@no_temporal@use_paper_num_label@roi_align@label_dep_layer@conv_lstm@sampleframe#13_model.npz
    use_paper_classnum = "use_paper_num_label" if args.use_paper_num_label else "all_avail_label"

    model_file_name = args.out + os.path.sep + \
                             'time_axis_rcnn_{0}_{1}_fold_{2}_{3}_model.npz'.format(args.database,
                                                                                args.fold, args.split_idx,
                                                                                use_paper_classnum)
    print(model_file_name)
    pretrained_optimizer_file_name = args.out + os.path.sep +\
                             'time_axis_rcnn_{0}_{1}_fold_{2}_{3}_optimizer.npz'.format(args.database,
                                                                                args.fold, args.split_idx,
                                                                                 use_paper_classnum)
    print(pretrained_optimizer_file_name)

    if os.path.exists(pretrained_optimizer_file_name):
        print("loading optimizer snatshot:{}".format(pretrained_optimizer_file_name))
        chainer.serializers.load_npz(pretrained_optimizer_file_name, optimizer)

    if os.path.exists(model_file_name):
        print("loading pretrained snapshot:{}".format(model_file_name))
        chainer.serializers.load_npz(model_file_name, model)

    print("only one GPU({0}) updater".format(args.gpu[0]))
    updater = chainer.training.StandardUpdater(train_iter, optimizer, device=args.gpu,
                          converter=lambda batch, device: concat_examples(batch, device, padding=0))

    trainer = training.Trainer(
        updater, (args.epoch, 'epoch'), out=args.out)

    trainer.extend(
        chainer.training.extensions.snapshot_object(optimizer, filename=os.path.basename(pretrained_optimizer_file_name)),
        trigger=(args.snapshot, 'iteration'))

    trainer.extend(
        chainer.training.extensions.snapshot_object(model,
                                                    filename=os.path.basename(model_file_name)),
        trigger=(args.snapshot, 'iteration'))

    log_interval = 100, 'iteration'
    print_interval = 10, 'iteration'
    plot_interval = 10, 'iteration'
    if args.optimizer != "Adam" and args.optimizer != "AdaDelta":
        trainer.extend(chainer.training.extensions.ExponentialShift('lr', 0.1),
                       trigger=(10, 'epoch'))
    elif args.optimizer == "Adam":
        trainer.extend(chainer.training.extensions.ExponentialShift("alpha", 0.1, optimizer=optimizer), trigger=(10, 'epoch'))
    if args.optimizer != "AdaDelta":
        trainer.extend(chainer.training.extensions.observe_lr(),
                       trigger=log_interval)
    trainer.extend(chainer.training.extensions.LogReport(trigger=log_interval,log_name="log_{0}_{1}_fold_{2}_{3}.log".format(
                                                                                        args.database, args.fold, args.split_idx,
                                                                                        use_paper_classnum)))
    trainer.extend(chainer.training.extensions.PrintReport(
        ['iteration', 'epoch', 'elapsed_time', 'lr',
         'main/loss','main/roi_loc_loss',
         'main/roi_cls_loss',
         'main/rpn_loc_loss',
         'main/rpn_cls_loss',
         'main/accuracy',
         ]), trigger=print_interval)
    trainer.extend(chainer.training.extensions.ProgressBar(update_interval=100))

    if chainer.training.extensions.PlotReport.available():
        trainer.extend(
            chainer.training.extensions.PlotReport(
                ['main/loss'],
                file_name='loss_{0}_{1}_fold_{2}_{3}.png'.format(args.database, args.fold, args.split_idx,
                                                        use_paper_classnum), trigger=plot_interval
            ),
            trigger=plot_interval
        )
        trainer.extend(
            chainer.training.extensions.PlotReport(
                ['main/accuracy'],
                file_name='accuracy_{0}_{1}_fold_{2}_{3}.png'.format(args.database, args.fold, args.split_idx,
                                                        use_paper_classnum), trigger=plot_interval
            ),
            trigger=plot_interval
        )

    trainer.run()
    # cProfile.runctx("trainer.run()", globals(), locals(), "Profile.prof")
    # s = pstats.Stats("Profile.prof")
    # s.strip_dirs().sort_stats("time").print_stats()




if __name__ == '__main__':
    main()
