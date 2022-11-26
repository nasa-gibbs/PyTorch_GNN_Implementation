import math
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


#-------------------------------------------------
# Aggregators

class AggregationLayer(nn.Module):
    def __init__(self, input_dim=None, output_dim=None):
        """
        Parameters
        ----------
        input_dim : int or None.
            Dimension of input node features. Used for defining fully
            connected layer in pooling aggregators. Default: None.
        output_dim : int or None
            Dimension of output node features. Used for defining fully
            connected layer in pooling aggregators. Currently only works when
            input_dim = output_dim. Default: None.
        """
        super().__init__()
        self.input_dim = input_dim
        self.output_dim = output_dim
    
    def forward(self, features, nodes, mapping, rows, num_samples=25):
        """
        Parameters
        ----------
        features : torch.Tensor
            An (n' x input_dim) tensor of input node features.
        nodes : numpy array
            nodes is a numpy array of nodes in the current layer of the computation graph.
        mapping : dict
            mapping is a dictionary mapping node v (labelled 0 to |V|-1) to
            its position in the layer of nodes in the computationn graph
            before nodes. For example, if the layer before nodes is [2,5],
            then mapping[2] = 0 and mapping[5] = 1.
        rows : numpy array
            rows[i] is an array of neighbors of node i which is present in nodes.
        num_samples : int
            Number of neighbors to sample while aggregating. Default: 25.
        Returns
        -------
        out : torch.Tensor
            An (len(nodes) x output_dim) tensor of output node features.
            Currently only works when output_dim = input_dim.
        """
        _choice, _len, _min = np.random.choice, len, min
        mapped_rows = [np.array([mapping[v] for v in row], dtype=np.int64) for row in rows]
        if num_samples == -1:
            sampled_rows = mapped_rows
        else:
            sampled_rows = [_choice(row, _min(_len(row), num_samples), _len(row) < num_samples) for row in mapped_rows]

        n = _len(nodes)
        if self.__class__.__name__ == 'LSTMAggregationLayer':
            out = torch.zeros(n, 2*self.output_dim).to(self.device)
        else:
            out = torch.zeros(n, self.output_dim).to(self.device)
        for i in range(n):
            if _len(sampled_rows[i]) != 0:
                out[i, :] = self.aggregate(features[sampled_rows[i], :])
        return out


    def aggregate(self, features):
        """
        Parameters
        ----------
        Returns
        -------
        """
        raise NotImplementedError


class MeanAggregationLayer(AggregationLayer):
    def aggregate(self, features):
        """
        Parameters
        ----------
        features : torch.Tensor
            Input features.
        Returns
        -------
        Aggregated feature.
        """
        return torch.mean(features, dim=0)


class LSTMAggregationLayer(AggregationLayer):
    def __init__(self, input_dim, output_dim, device='cpu'):
        """
        Parameters
        ----------
        input_dim : int
            Dimension of input node features. Used for defining LSTM layer.
        output_dim : int
            Dimension of output node features. Used for defining LSTM layer. Currently only works when output_dim = input_dim.
        """
        # super(LSTMAggregator, self).__init__(input_dim, output_dim, device)
        super().__init__(input_dim, output_dim, device)
        self.lstm = nn.LSTM(input_dim, output_dim, bidirectional=True, batch_first=True)

    def aggregate(self, features):
        """
        Parameters
        ----------
        features : torch.Tensor
            Input features.
        Returns
        -------
        Aggregated feature.
        """
        perm = np.random.permutation(np.arange(features.shape[0]))
        features = features[perm, :]
        features = features.unsqueeze(0)

        out, _ = self.lstm(features)
        out = out.squeeze(0)
        out = torch.sum(out, dim=0)

        return out


#------------------------------------------------------------
# Pooling Aggregators

class PoolingAggregationLayer(AggregationLayer):

    def __init__(self, input_dim, output_dim, device='cpu'):
        """
        Parameters
        ----------
        input_dim : int
            Dimension of input node features. Used for defining fully connected layer.
        output_dim : int
            Dimension of output node features. Used for defining fully connected layer. Currently only works when output_dim = input_dim.
        """
        super().__init__(input_dim, output_dim, device)

        self.fc1 = nn.Linear(input_dim, output_dim)
        self.relu = nn.ReLU()

    def aggregate(self, features):
        """
        Parameters
        ----------
        features : torch.Tensor
            Input features.
        Returns
        -------
        Aggregated feature.
        """
        out = self.relu(self.fc1(features))
        return self._pool_fn(out)

    def _pool_fn(self, features):
        """
        Parameters
        ----------
        Returns
        -------
        """
        raise NotImplementedError


class MaxPoolingAggregationLayer(PoolingAggregationLayer):
    def _pool_fn(self, features):
        """
        Parameters
        ----------
        features : torch.Tensor
            Input features.
        Returns
        -------
        Aggregated feature.
        """
        return torch.max(features, dim=0)[0]


class MeanPoolingAggregator(PoolingAggregationLayer):
    def _pool_fn(self, features):
        """
        Parameters
        ----------
        features : torch.Tensor
            Input features.
        Returns
        -------
        Aggregated feature.
        """
        return torch.mean(features, dim=0)


#------------------------------------------------------------
# GraphSAGE model

class GraphSAGE(nn.Module):
    def __init__(
        self, 
        input_dim, 
        hidden_dims, 
        output_dim,
        agg_class='maxpooling', 
        dropout=0.5, 
        num_samples=25
    ) -> None:
        """
        Parameters
        ----------
        input_dim : int
            Dimension of input node features.
        hidden_dims : list of ints
            Dimension of hidden layers. Must be non empty.
        output_dim : int
            Dimension of output node features.
        agg_class : A string that indicates the aggregator class to be used.
            Aggregator. One of the aggregator classes imported at the top of
            this module.
            Should be one of 'mean', 'mean-pooling', 'max-pooling', 'lstm'.
            Default: 'max-pooling'.
        dropout : float
            Dropout rate. Default: 0.5.
        num_samples : int
            Number of neighbors to sample while aggregating. Default: 25.
        """
        super().__init__()
        assert agg_class in ['mean', 'mean-pooling', 'max-pooling', 'lstm']

        self.input_dim = input_dim
        self.hidden_dims = hidden_dims
        self.output_dim = output_dim
        self.agg_class = agg_class
        self.num_samples = num_samples
        self.num_layers = len(hidden_dims) + 1

        aggregation_layer = {
            'mean': MeanAggregationLayer,
            'mean-pooling': MeanPoolingAggregator,
            'max-pooling': MaxPoolingAggregationLayer,
            'lstm': LSTMAggregationLayer
        }[agg_class]

        self.aggregators = nn.ModuleList([aggregation_layer(input_dim, input_dim)])
        self.aggregators.extend([aggregation_layer(dim, dim) for dim in hidden_dims])

        c = 3 if aggregation_layer == LSTMAggregationLayer else 2

        self.fcs = nn.ModuleList([nn.Linear(c*input_dim, hidden_dims[0])])
        self.fcs.extend([nn.Linear(c*hidden_dims[i-1], hidden_dims[i]) for i in range(1, len(hidden_dims))])
        self.fcs.extend([nn.Linear(c*hidden_dims[-1], output_dim)])

        self.bns = nn.ModuleList([nn.BatchNorm1d(hidden_dim) for hidden_dim in hidden_dims])

        self.dropout = nn.Dropout(dropout)

        self.relu = nn.ReLU()
    
    
    def forward(self, features, node_layers, mappings, rows):
        """
        Parameters
        ----------
        features : torch.Tensor
            An (n' x input_dim) tensor of input node features.
        node_layers : list of numpy array
            node_layers[i] is an array of the nodes in the ith layer of the
            computation graph.
        mappings : list of dictionary
            mappings[i] is a dictionary mapping node v (labelled 0 to |V|-1)
            in node_layers[i] to its position in node_layers[i]. For example,
            if node_layers[i] = [2,5], then mappings[i][2] = 0 and
            mappings[i][5] = 1.
        rows : numpy array
            rows[i] is an array of neighbors of node i.
        Returns
        -------
        out : torch.Tensor
            An (len(node_layers[-1]) x output_dim) tensor of output node features.
        """
        out = features
        for k in range(self.num_layers):
            nodes = node_layers[k+1]
            mapping = mappings[k]
            init_mapped_nodes = np.array([mappings[0][v] for v in nodes], dtype=np.int64)
            cur_rows = rows[init_mapped_nodes]
            aggregate = self.aggregators[k](out, nodes, mapping, cur_rows,
                                            self.num_samples)
            cur_mapped_nodes = np.array([mapping[v] for v in nodes], dtype=np.int64)
            out = torch.cat((out[cur_mapped_nodes, :], aggregate), dim=1)
            out = self.fcs[k](out)
            if k+1 < self.num_layers:
                out = self.relu(out)
                out = self.bns[k](out)
                out = self.dropout(out)
                out = out.div(out.norm(dim=1, keepdim=True)+1e-6)

        return out
