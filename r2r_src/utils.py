''' Utils for io, language, connectivity graphs etc '''

import os
import sys
import re
from param import args
if args.upload:
    sys.path.insert(0, '/R2R-Aux/build')
else:
    sys.path.insert(0, 'build')
import MatterSim
import string
import json
import time
import math
from collections import Counter, defaultdict
import numpy as np
import networkx as nx
from param import args
if args.upload:
    sys.path.insert(0, '/R2R-Aux/build')
else:
    sys.path.insert(0, 'build')
import subprocess

from polyaxon_client.tracking import get_data_paths

# padding, unknown word, end of sentence
base_vocab = ['<PAD>', '<UNK>', '<EOS>']
padding_idx = base_vocab.index('<PAD>')

def load_nav_graphs(scans):
    ''' Load connectivity graph for each scan '''

    def distance(pose1, pose2):
        ''' Euclidean distance between two graph poses '''
        return ((pose1['pose'][3]-pose2['pose'][3])**2\
          + (pose1['pose'][7]-pose2['pose'][7])**2\
          + (pose1['pose'][11]-pose2['pose'][11])**2)**0.5

    graphs = {}
    for scan in scans:
        with open('connectivity/%s_connectivity.json' % scan) as f:
            G = nx.Graph()
            positions = {}
            data = json.load(f)
            for i,item in enumerate(data):
                if item['included']:
                    for j,conn in enumerate(item['unobstructed']):
                        if conn and data[j]['included']:
                            positions[item['image_id']] = np.array([item['pose'][3], 
                                    item['pose'][7], item['pose'][11]]);
                            assert data[j]['unobstructed'][i], 'Graph should be undirected'
                            G.add_edge(item['image_id'],data[j]['image_id'],weight=distance(item,data[j]))
            nx.set_node_attributes(G, values=positions, name='position')
            graphs[scan] = G
    return graphs

def progress_generator(mask):
    mask = ~mask # [True, True, False]
    counter = mask.clone()
    counter = torch.sum(counter, dim=1).float()
    unit = 1 / counter
    progress = torch.ones_like(mask).cuda()
    progress = torch.cumsum(progress, dim=1).float()
    progress = progress * unit.unsqueeze(1).expand(mask.shape)
    progress = progress * mask.float()
    return progress

def gt_words(obs):
    """
    See "utils.Tokenizer.encode_sentence(...)" for "instr_encoding" details
    """
    seq_tensor = np.array([ob['instr_encoding'] for ob in obs])
    return torch.from_numpy(seq_tensor).cuda()

def load_datasets(splits):
    """

    :param splits: A list of split.
        if the split is "something@5000", it will use a random 5000 data from the data
    :return:
    """
    import random
    data = []
    old_state = random.getstate()
    for split in splits:
        # It only needs some part of the dataset?
        components = split.split("@")
        number = -1
        if len(components) > 1:
            split, number = components[0], int(components[1])

        # Load Json
        # if split in ['train', 'val_seen', 'val_unseen', 'test',
        #              'val_unseen_half1', 'val_unseen_half2', 'val_seen_half1', 'val_seen_half2']:       # Add two halves for sanity check
        if "/" not in split:
            if args.upload:
                with open(get_sync_dir(os.path.join(args.upload_path,'tasks/R2R/data/R2R_%s.json' % split))) as f:
                    new_data = json.load(f)
            else:
                with open(os.path.join(args.R2R_Aux_path,'tasks/R2R/data/R2R_%s.json' % split)) as f:
                    new_data = json.load(f)
        else:
            with open(split) as f:
                new_data = json.load(f)

        # Partition
        if number > 0:
            random.seed(0)              # Make the data deterministic, additive
            random.shuffle(new_data)
            new_data = new_data[:number]

        # Join
        data += new_data
    random.setstate(old_state)      # Recover the state of the random generator
    return data


class Tokenizer(object):
    ''' Class to tokenize and encode a sentence. '''
    SENTENCE_SPLIT_REGEX = re.compile(r'(\W+)')  # Split on any non-alphanumeric character

    def __init__(self, vocab=None, encoding_length=20):
        self.encoding_length = encoding_length
        self.vocab = vocab
        self.index_to_embed = {}
        # embed="tasks/R2R/data/trainval_glove.json"
        # if embed:
        #   self.index_to_embed = json.load(open( "%s" % embed , "r"))
        #  new_i = defaultdict(lambda: self.index_to_embed[1])
        # new_i.update(self.index_to_embed)
        # self.index_to_embed=new_i
        self.word_to_index = {}
        self.index_to_word = {}
        self.index_to_embed = {}
        if vocab:
            for i, word in enumerate(vocab):
                self.word_to_index[word] = i
            new_w2i = defaultdict(lambda: self.word_to_index['<UNK>'])
            new_w2i.update(self.word_to_index)
            self.word_to_index = new_w2i
            for key, value in self.word_to_index.items():
                self.index_to_word[value] = key

        old = self.vocab_size()
        # self.add_word('<BOS>')
        assert self.vocab_size() == old
        # self.get_glove()
        print("OLD_VOCAB_SIZE", old)
        print("VOCAB_SIZE", self.vocab_size())
        # print("VOACB", len(vocab))

    def get_glove(self, split):
        print("Extracting word embeddings from pretrained glove model")
        # if not os.path.exists("tasks/R2R/data/glove.json"):
        # print("...getting glove embeddings")
        f = open("tasks/R2R/data/glove.42B.300d.txt", 'r', encoding="utf-8")
        glove_embed = self.convert_dict(f)
        for key, value in self.word_to_index.items():
            if key in glove_embed:
                # self.word_to_embed[key] = glove_embed[key]
                self.index_to_embed[value] = glove_embed[key]
        self.index_to_embed[self.word_to_index['<PAD>']] = [str((self.vocab_size() - self.word_to_index[
            '<PAD>']) / self.vocab_size())] * 300
        self.index_to_embed[self.word_to_index['<UNK>']] = ["0"] * 300
        self.index_to_embed[self.word_to_index['<EOS>']] = ["1"] * 300
        self.index_to_embed[self.word_to_index['<BOS>']] = ["-1"] * 300
        new_i2e = defaultdict(lambda: self.index_to_embed[self.word_to_index['<UNK>']])
        new_i2e.update(self.index_to_embed)
        self.index_to_embed = new_i2e
        # with open("tasks/R2R/data/glove.json", "w") as fi:
        # fi.write(json.dumps(self.index_to_embed))
        # print("Embeddings saved in tasks/R2R/data/glove.json")
        # else:
        # self.index_to_embed=json.load(open("tasks/R2R/data/glove.json", "r") )

    def convert_dict(self, f):
        embeddings_dict = {}
        for line in f:
            values = line.split()
            word = values[0]
            vector = (values[1:])
            embeddings_dict[word] = vector
        return embeddings_dict


    def finalize(self):
        """
        This is used for debug
        """
        self.word_to_index = dict(self.word_to_index)   # To avoid using mis-typing tokens

    def add_word(self, word):
        assert word not in self.word_to_index
        self.word_to_index[word] = self.vocab_size()    # vocab_size() is the
        self.index_to_word[self.vocab_size()] = word

    @staticmethod
    def split_sentence(sentence):
        ''' Break sentence into a list of words and punctuation '''
        toks = []
        for word in [s.strip().lower() for s in Tokenizer.SENTENCE_SPLIT_REGEX.split(sentence.strip()) if len(s.strip()) > 0]:
            # Break up any words containing punctuation only, e.g. '!?', unless it is multiple full stops e.g. '..'
            if all(c in string.punctuation for c in word) and not all(c in '.' for c in word):
                toks += list(word)
            else:
                toks.append(word)
        return toks

    def vocab_size(self):
        return len(self.index_to_word)

    def encode_sentence(self, sentence, max_length=None):
        if max_length is None:
            max_length = self.encoding_length
        if len(self.word_to_index) == 0:
            sys.exit('Tokenizer has no vocab')

        encoding = [self.word_to_index['<BOS>']]
        for word in self.split_sentence(sentence):
            encoding.append(self.word_to_index[word])   # Default Dict
        encoding.append(self.word_to_index['<EOS>'])

        if len(encoding) <= 2:
            return None
        #assert len(encoding) > 2

        if len(encoding) < max_length:
            encoding += [self.word_to_index['<PAD>']] * (max_length-len(encoding))  # Padding
        elif len(encoding) > max_length:
            encoding[max_length - 1] = self.word_to_index['<EOS>']                  # Cut the length with EOS

        return np.array(encoding[:max_length])

    def decode_sentence(self, encoding, length=None):
        sentence = []
        if length is not None:
            encoding = encoding[:length]
        for ix in encoding:
            if ix == self.word_to_index['<PAD>']:
                break
            else:
                sentence.append(self.index_to_word[ix])
        return " ".join(sentence)

    def shrink(self, inst):
        """
        :param inst:    The id inst
        :return:  Remove the potential <BOS> and <EOS>
                  If no <EOS> return empty list
        """
        if len(inst) == 0:
            return inst
        end = np.argmax(np.array(inst) == self.word_to_index['<EOS>'])     # If no <EOS>, return empty string
        if len(inst) > 1 and inst[0] == self.word_to_index['<BOS>']:
            start = 1
        else:
            start = 0
        # print(inst, start, end)
        return inst[start: end]


def build_vocab(splits=['train'], min_count=5, start_vocab=base_vocab):
    ''' Build a vocab, starting with base vocab containing a few useful tokens. '''
    count = Counter()
    t = Tokenizer()
    data = load_datasets(splits)
    for item in data:
        for instr in item['instructions']:
            count.update(t.split_sentence(instr))
    vocab = list(start_vocab)
    for word,num in count.most_common():
        if num >= min_count:
            vocab.append(word)
        else:
            break
    return vocab


def write_vocab(vocab, path):
    print('Writing vocab of size %d to %s' % (len(vocab),path))
    with open(path, 'w') as f:
        for word in vocab:
            f.write("%s\n" % word)


def read_vocab(path):
    with open(path) as f:
        vocab = [word.strip() for word in f.readlines()]
    return vocab


def asMinutes(s):
    m = math.floor(s / 60)
    s -= m * 60
    return '%dm %ds' % (m, s)


def timeSince(since, percent):
    now = time.time()
    s = now - since
    es = s / (percent)
    rs = es - s
    return '%s (- %s)' % (asMinutes(s), asMinutes(rs))

def read_img_features(feature_store):
    import csv
    import base64
    from tqdm import tqdm

    print("Start loading the image feature")
    start = time.time()

    if "detectfeat" in args.features:
        views = int(args.features[10:])
    else:
        views = 36

    args.views = views

    tsv_fieldnames = ['scanId', 'viewpointId', 'image_w', 'image_h', 'vfov', 'features']
    features = {}
    with open(feature_store, "r") as tsv_in_file:     # Open the tsv file.
        reader = csv.DictReader(tsv_in_file, delimiter='\t', fieldnames=tsv_fieldnames)
        for item in reader:
            long_id = item['scanId'] + "_" + item['viewpointId']
            features[long_id] = np.frombuffer(base64.decodestring(item['features'].encode('ascii')),
                                                   dtype=np.float32).reshape((views, -1))   # Feature of long_id is (36, 2048)

    print("Finish Loading the image feature from %s in %0.4f seconds" % (feature_store, time.time() - start))
    return features

def read_obj_dense_features(dense_obj_feat1, dense_obj_feat2, bbox, sparse_obj_feat, th):
    print("Start loading the object dense feature")
    start = time.time()
    obj_d_feat1 = np.load(dense_obj_feat1, allow_pickle=True).item()
    obj_d_feat2 = np.load(dense_obj_feat2, allow_pickle=True).item()
    obj_s_feat = np.load(sparse_obj_feat, allow_pickle=True).item()
    obj_d_feat = {**obj_d_feat1, **obj_d_feat2}
    bbox_feat = np.load(bbox, allow_pickle=True).item()
    viewpointIds = load_viewpointids()
    sum_feature = np.zeros(2048)
    sum_bbox = np.zeros(4)
    sum_e = np.zeros(4)
    sum_h = np.zeros(4)
    obj_num = 0
    none_num = 0

    # calculate the average feature&bbox for padding
    for n,(scanId, viewpointId) in enumerate(viewpointIds):
        long_id = scanId + '_' + viewpointId
        if len(obj_d_feat[long_id]['concat_prob']) > 0:
            for i,feat in enumerate(obj_d_feat[long_id]['concat_feature']):
                if obj_d_feat[long_id]['concat_prob'][i] < th:
                    continue
                sum_feature += feat
                assert  bbox_feat[long_id]['concat_viewIndex'] == obj_d_feat[long_id]['concat_viewIndex']
                sum_bbox += bbox_feat[long_id]['concat_bbox'][i]
                sum_h += obj_s_feat[long_id]['concat_angles_h'][i]
                sum_e += obj_s_feat[long_id]['concat_angles_e'][i]
                obj_num +=1
    avg_feature = sum_feature/obj_num
    avg_b = sum_bbox/obj_num
    avg_e = sum_e/obj_num
    avg_h = sum_h/obj_num
    avg_angle = obj_rad2reg_feature(avg_e, avg_h, 'dense')
    objs = {}
    for n, (scanId, viewpointId) in enumerate(viewpointIds):
        long_id = scanId + '_' + viewpointId
        flag = 0
        viewpoint_object = []
        for bb,bb_viewIndex, features, txt, viewIndex, prob, angles_h, angles_e in zip(
            bbox_feat[long_id]['concat_bbox'], bbox_feat[long_id]['concat_viewIndex'],
            obj_d_feat[long_id]['concat_feature'], obj_d_feat[long_id]['concat_text'],
            obj_d_feat[long_id]['concat_viewIndex'], obj_d_feat[long_id]['concat_prob'],
            obj_s_feat[long_id]['concat_angles_h'], obj_s_feat[long_id]['concat_angles_e']
        ):
            if prob < th:
                continue
            assert bb_viewIndex == viewIndex
            flag = 1
            angles = obj_rad2reg_feature(angles_h, angles_e, 'dense')
            viewpoint_object.append({'bbox':bb,'angles': angles, 'features': features,
                                     'text': txt, 'viewIndex': viewIndex, 'prob': prob})
        if not flag and (th!=1): # thresh out and pad average feature
            viewpoint_object.append({'bbox':avg_b, 'angles': avg_angle, 'features': avg_feature,
                                     'text': 'average', 'viewIndex':None, 'prob': None})
            none_num += 1
        if th == 1:
            viewpoint_object.append({'bbox': np.zeros(4),'angles': np.zeros(128), 'features': np.zeros(2048),
                                     'text': 'zero', 'viewIndex': None, 'prob': None})
            none_num += 1
        num_obj = len(viewpoint_object)
        #concat_angles = np.zeros((num_obj,128), np.float32)
        concat_bbox = np.zeros((num_obj, 4), np.float32)
        concat_dense_feature = np.zeros((num_obj, 2048))
        concat_text = [None] * num_obj
        concat_viewIndex = [None] * num_obj
        concat_prob = [None] * num_obj
        for n_obj, obj in enumerate(viewpoint_object):
            concat_bbox[n_obj] = obj['bbox']
            #concat_angles[n_obj] = obj['angles']
            concat_dense_feature[n_obj] = obj['features']
            concat_text[n_obj] = obj['text']
            concat_viewIndex[n_obj] = obj['viewIndex']
            concat_prob[n_obj] = obj['prob']
        objs[long_id] = {
            'concat_bbox': concat_bbox,
            #'concat_angles': concat_angles,
            'concat_feature': concat_dense_feature,
            'concat_text': concat_text,
            'concat_viewIndex': concat_viewIndex,
            'concat_prob': concat_prob
        }
    print("Finish loading the prob larger than %0.2f dense object dense feature from %s and %s in %0.4f seconds" % (
                                                th,dense_obj_feat1, dense_obj_feat2, time.time()-start))
    print("%d viewpoint get None object prob > % 0.2f"%(none_num, th))
    return  objs

def read_obj_sparse_features(sparse_obj_feat, th):
    print("Start loading the object sparse feature")
    start = time.time()
    obj_s_feat = np.load(sparse_obj_feat, allow_pickle=True).item()
    viewpointIds = load_viewpointids()
    sum_feature = np.zeros(300)
    sum_e = np.zeros(4)
    sum_h = np.zeros(4)
    obj_num = 0
    none_num = 0
    # s = 0
    for n, (scanId, viewpointId) in enumerate(viewpointIds):
        long_id = scanId + '_' + viewpointId
        if len(obj_s_feat[long_id]['concat_prob']) > 0:
            for i, feat in enumerate(obj_s_feat[long_id]['concat_feature']):
                if obj_s_feat[long_id]['concat_prob'][i] < th:
                    continue
                sum_feature += feat
                sum_e += obj_s_feat[long_id]['concat_angles_e'][i]
                sum_h += obj_s_feat[long_id]['concat_angles_h'][i]

                obj_num +=1
    avg_feature = sum_feature/obj_num
    avg_e = sum_e/obj_num
    avg_h = sum_h/obj_num
    avg_angle = obj_rad2reg_feature(avg_e, avg_h, 'sparse')

    objs = {}
    for n, (scanId, viewpointId) in enumerate(viewpointIds):
        long_id = scanId + '_' + viewpointId
        flag = 0
        viewpoint_object = []
        for angles_h,angles_e,features,txt,viewIndex,prob in zip(
            obj_s_feat[long_id]['concat_angles_h'],obj_s_feat[long_id]['concat_angles_e'],
            obj_s_feat[long_id]['concat_feature'],obj_s_feat[long_id]['concat_text'],
            obj_s_feat[long_id]['concat_viewIndex'],obj_s_feat[long_id]['concat_prob']
        ):
            if prob < th:
                continue
            flag = 1
            angles = obj_rad2reg_feature(angles_h, angles_e, 'sparse')
            viewpoint_object.append({'angles': angles, 'features': features,
                                  'text': txt,'viewIndex': viewIndex,'prob': prob})
        if not flag and (th != 1):
            viewpoint_object.append({'angles': avg_angle, 'features': avg_feature,
                                  'text': 'average', 'viewIndex': None, 'prob': None})
            none_num +=1
        if th == 1:
            viewpoint_object.append({'angles': np.zeros(16),  'features': np.zeros(300),
                                     'text': 'average', 'viewIndex': None, 'prob': None})
            none_num +=1
        num_obj = len(viewpoint_object)
        concat_angles = np.zeros((num_obj,16), np.float32)
        concat_dense_feature = np.zeros((num_obj, 300))
        concat_text = [None] * num_obj
        concat_viewIndex = [None] * num_obj
        concat_prob = [None] * num_obj
        for n_obj, obj in enumerate(viewpoint_object):
            concat_angles[n_obj] = obj['angles']
            concat_dense_feature[n_obj] = obj['features']
            concat_text[n_obj] = obj['text']
            concat_viewIndex[n_obj] = obj['viewIndex']
            concat_prob[n_obj] = obj['prob']
        objs[long_id] = {
            'concat_angles': concat_angles,
            'concat_feature': concat_dense_feature,
            'concat_text': concat_text,
            'concat_viewIndex': concat_viewIndex,
            'concat_prob': concat_prob
        }


    print("Finish Loading the prob larger than %0.2f sparse object feature from %s in %0.4f seconds" % (
                                                th,sparse_obj_feat,time.time() - start))
    print("%d viewpoint get None object prob > %0.2f" %(none_num, th))
    return objs

def load_viewpointids():
    viewpointIds = []
    with open('connectivity/scans.txt') as f:
        scans = [scan.strip() for scan in f.readlines()]
        for scan in scans:
            with open('connectivity/%s_connectivity.json' % scan) as j:
                data = json.load(j)
                for item in data:
                    if item['included']:
                        viewpointIds.append((scan, item['image_id']))
    print('Loaded %d viewpoints' % len(viewpointIds))
    return viewpointIds

def read_candidates(candidates_store):
    import csv
    import base64
    from collections import defaultdict
    print("Start loading the candidate feature")

    start = time.time()

    TSV_FIELDNAMES = ['scanId', 'viewpointId', 'heading', 'elevation', 'next', 'pointId', 'idx', 'feature']
    candidates = defaultdict(lambda: list())
    items = 0
    with open(candidates_store, "r") as tsv_in_file:     # Open the tsv file.
        reader = csv.DictReader(tsv_in_file, delimiter='\t', fieldnames=TSV_FIELDNAMES)
        for item in reader:
            long_id = item['scanId'] + "_" + item['viewpointId']
            candidates[long_id].append(
                {'heading': float(item['heading']),
                 'elevation': float(item['elevation']),
                 'scanId': item['scanId'],
                 'viewpointId': item['next'],
                 'pointId': int(item['pointId']),
                 'idx': int(item['idx']) + 1,   # Because a bug in the precompute code, here +1 is important
                 'feature': np.frombuffer(
                     base64.decodestring(item['feature'].encode('ascii')),
                     dtype=np.float32)
                    }
            )
            items += 1

    for long_id in candidates:
        assert (len(candidates[long_id])) != 0

    assert sum(len(candidate) for candidate in candidates.values()) == items

    # candidate = candidates[long_id]
    # print(candidate)
    print("Finish Loading the candidates from %s in %0.4f seconds" % (candidates_store, time.time() - start))
    candidates = dict(candidates)
    return candidates

def add_exploration(paths):
    explore = json.load(open("tasks/R2R/data/exploration.json", 'r'))
    inst2explore = {path['instr_id']: path['trajectory'] for path in explore}
    for path in paths:
        path['trajectory'] = inst2explore[path['instr_id']] + path['trajectory']
    return paths

def angle_feature(heading, elevation):
    import math
    # twopi = math.pi * 2
    # heading = (heading + twopi) % twopi     # From 0 ~ 2pi
    # It will be the same
    return np.array([math.sin(heading), math.cos(heading),
                     math.sin(elevation), math.cos(elevation)] * (args.angle_feat_size // 4),
                    dtype=np.float32)

def obj_rad2reg_feature(heading, elevation, obj):
    import math
    result = []
    for h in heading:
        result.append(math.sin(h))
        result.append(math.cos(h))
    for e in elevation:
        result.append(math.sin(e))
        result.append(math.cos(h))
    if obj=='sparse':
        return np.array(result, dtype=np.float32)
    elif obj=='dense':
        return np.array(result*(args.angle_feat_size//16),dtype=np.float32)

def new_simulator():
    import MatterSim
    # Simulator image parameters
    WIDTH = 640
    HEIGHT = 480
    VFOV = 60

    sim = MatterSim.Simulator()
    sim.setRenderingEnabled(False)
    sim.setCameraResolution(WIDTH, HEIGHT)
    sim.setCameraVFOV(math.radians(VFOV))
    sim.setDiscretizedViewingAngles(True)
    sim.init()

    return sim

def get_point_angle_feature(baseViewId=0):
    sim = new_simulator()

    feature = np.empty((36, args.angle_feat_size), np.float32)
    base_heading = (baseViewId % 12) * math.radians(30)
    for ix in range(36):
        if ix == 0:
            sim.newEpisode('ZMojNkEp431', '2f4d90acd4024c269fb0efe49a8ac540', 0, math.radians(-30))
        elif ix % 12 == 0:
            sim.makeAction(0, 1.0, 1.0)
        else:
            sim.makeAction(0, 1.0, 0)

        state = sim.getState()
        assert state.viewIndex == ix

        heading = state.heading - base_heading

        feature[ix, :] = angle_feature(heading, state.elevation)
    return feature

def get_all_point_angle_feature():
    return [get_point_angle_feature(baseViewId) for baseViewId in range(36)]

def get_avg_point_angle_feature():
    return [np.average(get_point_angle_feature(baseViewId),axis=0) for baseViewId in range(36)]


def add_idx(inst):
    toks = Tokenizer.split_sentence(inst)
    return " ".join([str(idx)+tok for idx, tok in enumerate(toks)])

import signal
class GracefulKiller:
  kill_now = False
  def __init__(self):
    signal.signal(signal.SIGINT, self.exit_gracefully)
    signal.signal(signal.SIGTERM, self.exit_gracefully)

  def exit_gracefully(self,signum, frame):
    self.kill_now = True

from collections import OrderedDict

class Timer:
    def __init__(self):
        self.cul = OrderedDict()
        self.start = {}
        self.iter = 0

    def reset(self):
        self.cul = OrderedDict()
        self.start = {}
        self.iter = 0

    def tic(self, key):
        self.start[key] = time.time()

    def toc(self, key):
        delta = time.time() - self.start[key]
        if key not in self.cul:
            self.cul[key] = delta
        else:
            self.cul[key] += delta

    def step(self):
        self.iter += 1

    def show(self):
        total = sum(self.cul.values())
        for key in self.cul:
            print("%s, total time %0.2f, avg time %0.2f, part of %0.2f" %
                  (key, self.cul[key], self.cul[key]*1./self.iter, self.cul[key]*1./total))
        print(total / self.iter)


stop_word_list = [
    ",", ".", "and", "?", "!"
]


def stop_words_location(inst, mask=False):
    toks = Tokenizer.split_sentence(inst)
    sws = [i for i, tok in enumerate(toks) if tok in stop_word_list]        # The index of the stop words
    if len(sws) == 0 or sws[-1] != (len(toks)-1):     # Add the index of the last token
        sws.append(len(toks)-1)
    sws = [x for x, y in zip(sws[:-1], sws[1:]) if x+1 != y] + [sws[-1]]    # Filter the adjacent stop word
    sws_mask = np.ones(len(toks), np.int32)         # Create the mask
    sws_mask[sws] = 0
    return sws_mask if mask else sws

def get_segments(inst, mask=False):
    toks = Tokenizer.split_sentence(inst)
    sws = [i for i, tok in enumerate(toks) if tok in stop_word_list]        # The index of the stop words
    sws = [-1] + sws + [len(toks)]      # Add the <start> and <end> positions
    segments = [toks[sws[i]+1:sws[i+1]] for i in range(len(sws)-1)]       # Slice the segments from the tokens
    segments = list(filter(lambda x: len(x)>0, segments))     # remove the consecutive stop words
    return segments

def clever_pad_sequence(sequences, batch_first=True, padding_value=0):
    max_size = sequences[0].size()
    max_len, trailing_dims = max_size[0], max_size[1:]
    max_len = max(seq.size()[0] for seq in sequences)
    if batch_first:
        out_dims = (len(sequences), max_len) + trailing_dims
    else:
        out_dims = (max_len, len(sequences)) + trailing_dims
    if padding_value is not None:
        out_tensor = sequences[0].data.new(*out_dims).fill_(padding_value)
    for i, tensor in enumerate(sequences):
        length = tensor.size(0)
        # use index notation to prevent duplicate references to the tensor
        if batch_first:
            out_tensor[i, :length, ...] = tensor
        else:
            out_tensor[:length, i, ...] = tensor

    return out_tensor

import torch
def length2mask(length, size=None):
    batch_size = len(length)
    size = int(max(length)) if size is None else size
    mask = (torch.arange(size, dtype=torch.int64).unsqueeze(0).repeat(batch_size, 1)
                > (torch.LongTensor(length) - 1).unsqueeze(1)).cuda()
    return mask

def average_length(path2inst):
    length = []

    for name in path2inst:
        datum = path2inst[name]
        length.append(len(datum))
    return sum(length) / len(length)

def tile_batch(tensor, multiplier):
    _, *s = tensor.size()
    tensor = tensor.unsqueeze(1).expand(-1, multiplier, *(-1,) * len(s)).contiguous().view(-1, *s)
    return tensor

def viewpoint_drop_mask(viewpoint, seed=None, drop_func=None):
    local_seed = hash(viewpoint) ^ seed
    torch.random.manual_seed(local_seed)
    drop_mask = drop_func(torch.ones(2048).cuda())
    return drop_mask


class FloydGraph:
    def __init__(self):
        self._dis = defaultdict(lambda :defaultdict(lambda: 95959595))
        self._point = defaultdict(lambda :defaultdict(lambda: ""))
        self._visited = set()

    def distance(self, x, y):
        if x == y:
            return 0
        else:
            return self._dis[x][y]

    def add_edge(self, x, y, dis):
        if dis < self._dis[x][y]:
            self._dis[x][y] = dis
            self._dis[y][x] = dis
            self._point[x][y] = ""
            self._point[y][x] = ""

    def update(self, k):
        for x in self._dis:
            for y in self._dis:
                if x != y:
                    if self._dis[x][k] + self._dis[k][y] < self._dis[x][y]:
                        self._dis[x][y] = self._dis[x][k] + self._dis[k][y]
                        self._dis[y][x] = self._dis[x][y]
                        self._point[x][y] = k
                        self._point[y][x] = k
        self._visited.add(k)

    def visited(self, k):
        return (k in self._visited)

    def path(self, x, y):
        """
        :param x: start
        :param y: end
        :return: the path from x to y [v1, v2, ..., v_n, y]
        """
        if x == y:
            return []
        if self._point[x][y] == "":     # Direct edge
            return [y]
        else:
            k = self._point[x][y]
            # print(x, y, k)
            # for x1 in (x, k, y):
            #     for x2 in (x, k, y):
            #         print(x1, x2, "%.4f" % self._dis[x1][x2])
            return self.path(x, k) + self.path(k, y)

def get_sync_dir(file):
    # 只改source_data就好
    source_data = file

    sync_source_dir = os.path.join(get_data_paths()['ceph'], source_data.strip('/'))
    sync_dest_dir = os.path.join(get_data_paths()['host-path'],
                                 os.path.dirname(source_data.strip('/')))

    # 确保同步目录存在, 防止拷贝文件时异常
    if not os.path.exists(sync_dest_dir):
        cmd_line = "mkdir -p {0}".format(sync_dest_dir)
        subprocess.call(cmd_line.split())

    data_dir = os.path.join(get_data_paths()['host-path'], source_data.strip('/'))

    if not os.path.exists(data_dir):
        # --info=progress2需要rsync3.1+的版本支持
        cmd_line = "rsync -a {0} {1}".format(sync_source_dir, sync_dest_dir)
        subprocess.call(cmd_line.split())

    return data_dir

