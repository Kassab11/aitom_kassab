from sklearn.metrics.cluster import contingency_matrix
from scipy.optimize import linear_sum_assignment
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import StepLR
from torch.nn import init
import torch.nn.functional as F
from sklearn.mixture import GaussianMixture
from sklearn.decomposition import PCA    
import sys, multiprocessing, importlib, pickle, time
from multiprocessing.pool import Pool  
from tqdm.auto import tqdm
from torchvision.transforms import Normalize

from aitom.classify.deep.unsupervised.disca.util import *

import matplotlib.pyplot as plt
from torch.optim.lr_scheduler import MultiStepLR
from sklearn.metrics import homogeneity_completeness_v_measure
import ast
import argparse
import os



import warnings
warnings.filterwarnings("ignore")

Config = None



class Subtomogram_Dataset:
    def __init__(self, train_data, label_one_hot):
        self.train_data = train_data
        self.label_one_hot = label_one_hot

    def __len__(self):
        return len(self.train_data)

    def __getitem__(self, index):
        features = self.train_data[index]
        labels = self.label_one_hot[index]

        features = torch.FloatTensor(features)
        labels = torch.FloatTensor(labels)

        return features, labels

def align_cluster_index(ref_cluster, map_cluster):
    """                                                                                                                                                                            
    remap cluster index according the the ref_cluster.                                                                                                                                    
    both inputs must have same number of unique cluster index values.                                                                                                                      
    """

    ref_values = np.unique(ref_cluster)
    map_values = np.unique(map_cluster)

    if ref_values.shape[0] != map_values.shape[0]:
        print('error: both inputs must have same number of unique cluster index values.')
        return ()
    cont_mat = contingency_matrix(ref_cluster, map_cluster)

    row_ind, col_ind = linear_sum_assignment(len(ref_cluster) - cont_mat)

    map_cluster_out = map_cluster.copy()

    for i in ref_values:
        map_cluster_out[map_cluster == col_ind[i]] = i

    return map_cluster_out



def DDBI(features, labels):
    """                                                                                                                                                                            
    compute the Distortion-based Davies-Bouldin index defined in Equ 1 of the Supporting Information.                                                                                                        
    """

    means_init = np.array([np.mean(features[labels == i], 0) for i in np.unique(labels)])
    precisions_init = np.array(
        [np.linalg.inv(np.cov(features[labels == i].T) + Config.reg_covar * np.eye(features.shape[1])) for i in
         np.unique(labels)])

    T = np.array([np.mean(np.diag(
        (features[labels == i] - means_init[i]).dot(precisions_init[i]).dot((features[labels == i] - means_init[i]).T)))
                  for i in np.unique(labels)])

    D = np.array(
        [np.diag((means_init - means_init[i]).dot(precisions_init[i]).dot((means_init - means_init[i]).T)) for i in
         np.unique(labels)])

    DBI_matrix = np.zeros((len(np.unique(labels)), len(np.unique(labels))))

    for i in range(len(np.unique(labels))):
        for j in range(len(np.unique(labels))):
            if i != j:
                DBI_matrix[i, j] = (T[i] + T[j]) / (D[i, j] + D[j, i])

    DBI = np.mean(np.max(DBI_matrix, 0))

    return DBI

class YOPOFeatureModel(nn.Module):

    def __init__(self):
        super(YOPOFeatureModel, self).__init__()

        self.dropout = nn.Dropout(0.5)
        self.m1 = self.get_block(1, 64)
        self.m2 = self.get_block(64, 80)
        self.m3 = self.get_block(80, 96)
        self.m4 = self.get_block(96, 112)
        self.m5 = self.get_block(112, 128)
        self.m6 = self.get_block(128, 144)
        self.m7 = self.get_block(144, 160)
        self.m8 = self.get_block(160, 176)
        self.m9 = self.get_block(176, 192)
        self.m10 = self.get_block(192, 208)
        self.batchnorm = torch.nn.BatchNorm3d(1360)
        self.linear = nn.Linear(
            in_features=1360,
            out_features=1024
        )
       
        self.weight_init(self)
        
    '''
	Initialising the model with blocks of layers.
	'''

    @staticmethod
    def get_block(input_channel_size, output_channel_size):
        return nn.Sequential(
            torch.nn.Conv3d(in_channels=input_channel_size,
                            out_channels=output_channel_size,
                            kernel_size=(3, 3, 3),
                            padding=0,
                            dilation=(1, 1, 1)),  
            torch.nn.ELU(),
            torch.nn.BatchNorm3d(output_channel_size)
        )

    '''
	Initialising weights of the model.
	'''

    @staticmethod
    def weight_init(self):
        for m in self.modules():
            if isinstance(m, nn.Conv3d) or isinstance(m, nn.Linear):
                nn.init.orthogonal_(m.weight)
                nn.init.zeros_(m.bias)

    '''
	Forward Propagation Pass.
	'''

    def forward(self, input_image):
        output = input_image.view(-1, 1, Config.image_size, Config.image_size, Config.image_size)
        output = self.dropout(output)
        output = self.m1(output)
        o1 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m2(output)
        o2 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m3(output)
        o3 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m4(output)
        o4 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m5(output)
        o5 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m6(output)
        o6 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m7(output)
        o7 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m8(output)
        o8 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m9(output)
        o9 = F.max_pool3d(output, kernel_size=output.size()[2:])
        output = self.m10(output)
        o10 = F.max_pool3d(output, kernel_size=output.size()[2:])

        m = torch.cat((o1, o2, o3, o4, o5, o6, o7, o8, o9, o10), dim=1)
        m = self.batchnorm(m)
        m = nn.Flatten()(m)
        m = self.linear(m)
        return m


def statistical_fitting(features, labels, candidateKs, K, reg_covar, i):

    pca = PCA(n_components=16)  
    features_pca = pca.fit_transform(features) 

    labels_K = [] 
    BICs = [] 
                                                                                                                                                            
    for k in candidateKs: 
        if k == K: 
            try:
                weights_init = np.array([np.sum(labels == j)/float(len(labels)) for j in range(k)]) 
                means_init = np.array([np.mean(features_pca[labels == j], 0) for j in range(k)]) 
                precisions_init = np.array([np.linalg.inv(np.cov(features_pca[labels == j].T) + reg_covar * np.eye(features_pca.shape[1])) for j in range(k)]) 
 
                gmm_0 = GaussianMixture(n_components=k, covariance_type='full', tol=0.001, reg_covar=reg_covar, max_iter=100, n_init=5, random_state=i,  
                                        weights_init=weights_init, means_init=means_init, precisions_init=precisions_init, init_params = 'random') 
 
                gmm_0.fit(features_pca) 
                labels_k_0 = gmm_0.predict(features_pca)

            except:     
                gmm_0 = GaussianMixture(n_components=k, covariance_type='full', tol=0.001, reg_covar=reg_covar, max_iter=100, n_init=5, random_state=i, init_params = 'random') 
                gmm_0.fit(features_pca) 
                labels_k_0 = gmm_0.predict(features_pca) 
                         
         
            gmm_1 = GaussianMixture(n_components=k, covariance_type='full', tol=0.001, reg_covar=reg_covar, max_iter=100, n_init=5, random_state=i, init_params = 'random') 
            gmm_1.fit(features_pca) 
            labels_k_1 = gmm_1.predict(features_pca) 
             
            m_select = np.argmin([gmm_0.bic(features_pca), gmm_1.bic(features_pca)]) 
             
            if m_select == 0: 
                labels_K.append(labels_k_0) 
                 
                BICs.append(gmm_0.bic(features_pca)) 
             
            else: 
                labels_K.append(labels_k_1) 
                 
                BICs.append(gmm_1.bic(features_pca)) 
         
        else: 
            gmm = GaussianMixture(n_components=k, covariance_type='full', tol=0.0001, reg_covar=reg_covar, max_iter=100, n_init=5, random_state=i, init_params = 'random') 
         
            gmm.fit(features_pca) 
            labels_k = gmm.predict(features_pca) 

            labels_K.append(labels_k) 
             
            BICs.append(gmm.bic(features_pca)) 
    
    labels_temp = remove_empty_cluster(labels_K[np.argmin(BICs)])                     
     
    K_temp = len(np.unique(labels_temp)) 
     
    if K_temp == K: 
        same_K = True 
    else: 
        same_K = False 
        K = K_temp     

    print('Estimated K:', K)
    
    return labels_temp, K, same_K, features_pca   



def convergence_check(i, M, labels_temp, labels, done):
    if i > 75:
        if np.sum(labels_temp == labels) / float(len(labels)) > 0.999:
            done = True

    i += 1
    if i == M:
        done = True

    labels = labels_temp

    return i, labels, done



def pickle_dump(o, path, protocol=2):
    with open(path, 'wb') as f:    pickle.dump(o, f, protocol=protocol)



def run_iterator(tasks, worker_num=multiprocessing.cpu_count(), verbose=False):

    if verbose:		print('parallel_multiprocessing()', 'start', time.time())

    worker_num = min(worker_num, multiprocessing.cpu_count())

    for i,t in tasks.items():
        if 'args' not in t:     t['args'] = ()
        if 'kwargs' not in t:     t['kwargs'] = {}
        if 'id' not in t:   t['id'] = i
        assert t['id'] == i

    completed_count = 0 
    if worker_num > 1:

        pool = Pool(processes = worker_num)
        pool_apply = []
        for i,t in tasks.items():
            aa = pool.apply_async(func=call_func, kwds={'t':t})

            pool_apply.append(aa)


        for pa in pool_apply:
            yield pa.get(99999)
            completed_count += 1

            if verbose:
                print('\r', completed_count, '/', len(tasks), end=' ')
                sys.stdout.flush()

        pool.close()
        pool.join()
        del pool

    else:

        for i,t in tasks.items():
            yield call_func(t)
            completed_count += 1

            if verbose:
                print('\r', completed_count, '/', len(tasks), end=' ')
                sys.stdout.flush()
	
    if verbose:		print('parallel_multiprocessing()', 'end', time.time())


    
run_batch = run_iterator #alias



def call_func(t):

    if 'func' in t:
        assert 'module' not in t
        assert 'method' not in t
        func = t['func']
    else:
        modu = importlib.import_module(t['module'])
        func = getattr(modu, t['method'])

    r = func(*t['args'], **t['kwargs'])
    return {'id':t['id'], 'result':r}



def random_rotation_matrix():
    m = np.random.random( (3,3) )
    u,s,v = np.linalg.svd(m)

    return u



def rotate3d_zyz(data, Inv_R, center=None, order=2):
    # Figure out the rotation center
    if center is None:
        cx = data.shape[0] / 2
        cy = data.shape[1] / 2
        cz = data.shape[2] / 2
    else:
        assert len(center) == 3
        (cx, cy, cz) = center

    
    from scipy import mgrid
    grid = mgrid[-cx:data.shape[0]-cx, -cy:data.shape[1]-cy, -cz:data.shape[2]-cz]
    temp = grid.reshape((3, int(grid.size / 3)))
    temp = np.dot(Inv_R, temp)
    grid = np.reshape(temp, grid.shape)
    grid[0] += cx
    grid[1] += cy
    grid[2] += cz

    # Interpolation
    from scipy.ndimage import map_coordinates
    d = map_coordinates(data, grid, order=order)

    return d



def data_augmentation(x_train, factor = 2):

    if factor > 1:

        x_train_augmented = []
        
        x_train_augmented.append(x_train)

        for f in range(1, factor):
            ts = {}        
            for i in range(len(x_train)):                       
                t = {}                                                
                t['func'] = rotate3d_zyz                                   
                                                      
                # prepare keyword arguments                                                                                                               
                args_t = {}                                                                                                                               
                args_t['data'] = x_train[i,:,:,:,0]                                                                                                                 
                args_t['Inv_R'] = random_rotation_matrix()                                                   
                                                                                                                                                                                                                                           
                t['kwargs'] = args_t                                                  
                ts[i] = t                                                       
                                                                      
            rs = run_batch(ts, worker_num=48)
            x_train_f = np.expand_dims(np.array([_['result'] for _ in rs]), -1)
            
            x_train_augmented.append(x_train_f)
            
        x_train_augmented = np.concatenate(x_train_augmented)
    
    else:
        x_train_augmented = x_train                        

        x_train[x_train == 0] = np.random.normal(loc=0.0, scale=1.0, size = np.sum(x_train == 0))

    return x_train_augmented



def one_hot(a, num_classes):
    return np.squeeze(np.eye(num_classes)[a.reshape(-1)])   



def smooth_labels(labels, factor=0.1):
    labels *= (1 - factor)
    labels += (factor / labels.shape[1])
 
    return labels           



def remove_empty_cluster(labels):
    labels_unique = np.unique(labels)
    for i in range(len(np.unique(labels))):
        labels[labels == labels_unique[i]] = i

    return labels



def prepare_training_data(x_train, labels, label_smoothing_factor):

    label_one_hot = one_hot(labels, len(np.unique(labels))) 
    
    index = np.array(range(x_train.shape[0] * Config.factor_use)) 

    np.random.shuffle(index)         
     
    x_train_augmented = data_augmentation(x_train, Config.factor_use) 
    
    x_train_permute = x_train_augmented[index].copy() 

    label_smoothing_factor *= 0.9 

    labels_augmented = np.tile(smooth_labels(label_one_hot, label_smoothing_factor), (2,1))               

    labels_permute = labels_augmented[index].copy() 

    return label_one_hot, x_train_permute, label_smoothing_factor, labels_permute

def load_pickle_file(path):

    with open(path, 'rb', 0) as f:
	    data = pickle.load(f, encoding='latin1')
    return data



class YOPOClassification(nn.Module):
    def __init__(self, num_labels, vector_size=1024):
        super(YOPOClassification, self).__init__()
        self.main_input = nn.Linear(vector_size, num_labels)
        self.softmax = nn.Softmax(dim=1)
    
    def forward(self, x):
        x = self.main_input(x)
        x = self.softmax(x)
        return x

class YOPO_Final_Model(nn.Module):
    def __init__(self, yopo_feature, yopo_classification):
        super(YOPO_Final_Model, self).__init__()
        self.feature_model = yopo_feature
        self.classification_model = yopo_classification
        
    def forward(self, input_image):
        features = self.feature_model(input_image)
        output = self.classification_model(features)
        return output

def update_output_layer(K, label_one_hot, batch_size, model_feature, features, lr, verbose=False):
    print('Updating output layer')
    model_classification = YOPOClassification(num_labels=K).to(Config.device)

    optim = torch.optim.NAdam(model_classification.parameters(), lr=0.0001, betas=(0.9, 0.999), eps=1e-08)
    criterion = nn.MultiMarginLoss()

    # Convert features and label_one_hot to PyTorch tensors

    dataset = Subtomogram_Dataset(features, label_one_hot)

    # Create a DataLoader for batch processing
    train_loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    model_loss = []
    

    for epoch in range(Config.yopo_iteration):
        model_classification.train()
        train_total = 0.0
        train_correct = 0.0
        epoch_loss = 0.0
        start_time = time.time()
        pbar = tqdm(train_loader, desc = 'Iterating over train data, Epoch: {}/{}'.format(epoch + 1, 10))
        for features, labels in pbar:
            features = features.to(Config.device)
            labels = labels.to(Config.device)

            pred = model_classification(features)

            optim.zero_grad()

            predicted = torch.argmax(pred, 1)
            labels_1 = torch.argmax(labels, 1)

            loss = criterion(pred, labels_1)
            epoch_loss += loss.item()

            loss.backward()
            optim.step()
            
            train_correct += (predicted == labels_1).sum().float().item()
            train_total += labels.size(0)

        exec_time = time.time() - start_time
        model_loss.append(epoch_loss)
        
        # calculate accuracy
        accuracy = train_correct / train_total
        
        if verbose:
            print('Epoch: {}/{} Loss: {:.4f} accuracy: {:.4f} In: {:.4f}s'.format(epoch + 1, 10, epoch_loss, accuracy, exec_time))

    model = YOPO_Final_Model(model_feature, model_classification)
    optimizer = torch.optim.NAdam(model.parameters(), lr=lr, betas=(0.9, 0.999), eps=1e-08)
    criterion = nn.MultiMarginLoss()
    
    print('Output layer updated')
    return model, optimizer, criterion

def image_normalization(img_list):
    ### img_list is a list cantains images, returns a list contains normalized images

    normalized_images = []
    print('Normalizing')
    for image in img_list:
        image = np.array(image)
        image = torch.tensor(image)
        normalize_single = Normalize(mean=[image.mean()], std=[image.std()])(image).tolist()
        normalized_images.append(normalize_single)
    print('Normalizing finished')
    return normalized_images

def learning_rate_scheduler_type(value):

    return value

def int_list(value):
    
    return [int(item) for item in value.split(',')]

def main():

    parser = argparse.ArgumentParser(description="Unsupervised Structural Pattern Mining with DISCA")
    parser.add_argument("--output_model_path", type=str, help="path to save output model",default="/l/users/mohamad.kassab/disca_test/model_torch_60.pth")
    parser.add_argument("--output_label_path", type=str, help="path to save labels", default="/l/users/mohamad.kassab/disca_test/label_path_torch_60.pickle")
    parser.add_argument("--gt_known", type=bool, help="if ground truth is available, set this flag to True", default=True)
    parser.add_argument("--path_to_gt", type=str, help="path to saved gt labels, use only if gt_known flag is true, else set as None", default="/l/users/mohamad.kassab/final_data/DISCA_DATA_60_0.1_id.pickle")
    parser.add_argument("--true_k", type=int, help="true number of classes, use only if gt_known flag is true, else set as None", default=5)		
    parser.add_argument("--candidatesKs", type=int_list, help="number of k candidates", default=[3,4,5,6])
    parser.add_argument("--img_size", type=int, help="size of input images",default=24)
    parser.add_argument("--batch_size", type=int, help="Batch Size",default=64)
    parser.add_argument("--training_data_path", type=str, help="path to training data", default="/l/users/mohamad.kassab/final_data/DISCA_DATA_60_0.1_v.pickle")
    parser.add_argument("--M", type=int, help="total number of iterations to train DISCA model", default=10)
    parser.add_argument("--yopo_iteration", type=int, help="number of epochs to train yopo network", default=10) 
    parser.add_argument("--lr", type=float, help="learning rate", default=1e-4)
    parser.add_argument("--label_smoothing_factor", type=float, help="label_smoothing_factor rate", default=0.2)    
    parser.add_argument("--reg_covar", type=float, help="reg_covar rate", default=0.00001)
    parser.add_argument("--normalize", type=bool, help="set flag to true if you want to normalize your training dataset", default=False)
    parser.add_argument("--factor_use", type=int, help="factor determining size of data augmentation", default=2)
    parser.add_argument("--class_id", type=ast.literal_eval, help="dictionary used for class mapping, if gt_known is False, set this flag to None", default = "{'1I6V': 0, '1QO1': 1, '3DY4': 2, '4V4A': 3, '5LQW': 4}")
    parser.add_argument("--checkpoint_dir", type=str, help="directory to save checkpoints", default="/l/users/mohamad.kassab/disca_test/checkpoint")
    parser.add_argument("--load_checkpoint", type=str, help="path to a checkpoint file to load and resume training", default=None)

    args = parser.parse_args()


    checkpoint_dir = args.checkpoint_dir
    os.makedirs(checkpoint_dir, exist_ok=True)

    # configuration
    class config:
        
        image_size = args.img_size  ### subtomogram size ###
        candidateKs = args.candidatesKs ### candidate number of clusters to test, it is also possible to set just one large K that overpartites the data

        batch_size = args.batch_size
        M = args.M  ### number of iterations ###
        lr = args.lr  ### Original CNN learning rate ###

        label_smoothing_factor = args.label_smoothing_factor  ### label smoothing factor ###
        reg_covar = args.reg_covar

        model_path = args.output_model_path  ### path for saving torch model, should be a pth file ###
        label_path = args.output_label_path  ### path for saving labels, should be a .pickle file ###

        factor_use = args.factor_use

        yopo_iteration = args.yopo_iteration

        device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
	    
    global Config
    Config = config()


    

	
    x_train = load_pickle_file(args.training_data_path)  ### load the x_train data, should be shape (n, shape_1, shape_2, shape_3, 1)
    gt = None

    if args.normalize:
        x_train = image_normalization(x_train)	   
	    
    x_train = np.expand_dims(x_train,-1)
    data_array_normalized = np.array(x_train)
    print('x_train.shape:',data_array_normalized.shape)
    
    
    x_train = torch.tensor(data_array_normalized, dtype=torch.float32)  ### load the x_train data, should be shape (n, 1, shape_1, shape_2, shape_3)

    if args.gt_known is not False:   
            gt = load_pickle_file(args.path_to_gt) ### load or define label ground truth here, if for simulated data
            class_mapping = args.class_id
            numerical_gt = [class_mapping[_] for _ in gt]
            gt = numerical_gt
    
    ### Generalized EM Process ###
    K = None
    labels = None
    DBI_best = float('inf')


    if args.load_checkpoint is not None:
        checkpoint = torch.load(args.load_checkpoint)
        K = checkpoint['K']
        labels = checkpoint['labels']
        DBI_best = checkpoint['DBI_best']
        i = checkpoint['iteration']
        done = checkpoint['done']
        model = checkpoint['model']
        print(f"Checkpoint loaded at iteration {i}")
    else:
        i = 0
        done = False
        print("Starting training from scratch")


    total_loss = []

    iterations = []
    losses = []
    accuracies = []
    execution_times = []
    learning_rate = []


    while not done:
        print('Iteration:', i)
        
    # feature extraction

        if i == 0:
            model_feature = YOPOFeatureModel().to(Config.device)
        else:
            model_feature = nn.Sequential(*list(model.children())[:-1])

        
        criterion = nn.MultiMarginLoss()
        optim = torch.optim.Adam(model_feature.parameters(), lr=Config.lr)

        features = np.empty((0, 1024))
        train_input_loader = DataLoader(x_train, batch_size = Config.batch_size, shuffle = False)
        
        train_pbar = tqdm(train_input_loader, desc='Feature extraction')
        model_feature.eval()    
        with torch.no_grad():
            for batch in train_pbar:
                batch = batch.to(Config.device)
                temp_features = model_feature(batch).detach().cpu().numpy()
                features = np.append(features, temp_features, axis=0) 

    ### Feature Clustering ###

        labels_temp, K, same_K, features_pca = statistical_fitting(features=features, labels=labels, candidateKs=Config.candidateKs, K=K, reg_covar=Config.reg_covar, i = i)
        
    ### Matching Clusters by Hungarian Algorithm ###

        if same_K:
            labels_temp = align_cluster_index(labels, labels_temp)

        i, labels, done = convergence_check(i=i, M=Config.M, labels_temp=labels_temp, labels=labels, done=done)

        print('Cluster sizes:', [np.sum(labels == k) for k in range(K)])

    ### Validate Clustering by distortion-based DBI ###

        DBI = DDBI(features_pca, labels)

        if DBI < DBI_best:
            if i > 1:
                torch.save(model, Config.model_path)  ### save model here ###
                print(f'Best Model Saved to: {Config.model_path}')

                pickle_dump(labels, Config.label_path)

            labels_best = labels  ### save current labels if DDBI improves ###

            DBI_best = DBI

        print('DDBI:', DBI, '############################################')

    ## Permute Samples ###   
        print('Prepearing training data')
        label_one_hot, x_train_permute, label_smoothing_factor, labels_permute = prepare_training_data(x_train=data_array_normalized, labels=labels, label_smoothing_factor=Config.label_smoothing_factor)
        print('Finished')
    ### Finetune new model with current estimated K ### 
        if not same_K: 
            model, optim, criterion = update_output_layer(K = K, label_one_hot = label_one_hot, batch_size = Config.batch_size, model_feature = model_feature, features=features, lr=Config.lr, verbose=False)

    ### CNN Training ### 
        print('Start CNN training')

        # learning rate decay
        scheduler  = StepLR(optim, step_size = 1, gamma = 0.95)

        dataset = Subtomogram_Dataset(x_train_permute, labels_permute)
        
        train_loader = DataLoader(dataset, batch_size=Config.batch_size, shuffle=True)  

        model.train()  
        iteration_loss = 0.0
        train_correct = 0.0
        train_total = 0.0
        start_time = time.time()

        for epoch in range(args.yopo_iteration):  # Loop for training epochs
            scheduler.step()  # Update learning rate at milestones
            print(f'Epoch {epoch + 1}, Learning Rate: {scheduler.get_last_lr()}')

        
        pbar = tqdm(train_loader, desc='Iterating over train data, Iteration: {}/{}'.format(i - 1, Config.M))
        for train, label in pbar:

            # pass to device
            train = train.to(Config.device)
            label = label.to(Config.device)

            pred = model(train)

            optim.zero_grad()

            label_1 = torch.argmax(label, 1)

            loss = criterion(pred, label_1)           
            iteration_loss += loss.item()

            loss.backward()
            optim.step()
            predicted = torch.argmax(pred, 1)
            train_correct += (predicted == label_1).sum().float().item()
            train_total += label.size(0)


        checkpoint = {
            'model': model,
            'K': K,
            'labels': labels,
            'DBI_best': DBI_best,
            'iteration': i,
            'done': done
            }
        checkpoint_path = os.path.join(checkpoint_dir, f'checkpoint_{i}.pth')
        torch.save(checkpoint, checkpoint_path)        

        total_loss.append(iteration_loss)

        # calculate accuracy
        #accuracy = train_correct / train_total

        exec_time = time.time() - start_time
        #print('Loss: {:.4f} Accuracy: {:.4f}  In: {:.4f}s'.format(iteration_loss, accuracy, exec_time))

        # Inside your loop
        #iterations.append(i)
        #losses.append(iteration_loss)
        #accuracies.append(accuracy)
        #execution_times.append(exec_time)
        #learning_rate.append(scheduler.get_last_lr()[0])

        if K == args.true_k and args.gt_known:   ### This is for evaluating accuracy on simulated data        
            labels_gt = align_cluster_index(gt, labels) 
 
            print('Accuracy:', np.sum(labels_gt == gt)/len(gt), '############################################')
 
        if args.gt_known: 
            homogeneity, completeness, v_measure = homogeneity_completeness_v_measure(gt, labels)                                                             
                                                      
            print('Homogeneity score:', homogeneity, '############################################')                                                          
            print('Completeness score:', completeness, '############################################')                                              
            print('V_measure:', v_measure, '############################################')  

if __name__ == "__main__":
  main()
