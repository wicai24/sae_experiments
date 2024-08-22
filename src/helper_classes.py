import abc
import json
import os
import time

import numpy as np
import torch
from circuitsvis.tokens import colored_tokens
from tqdm.auto import tqdm
from transformer_lens import utils as tl_utils

from .utils import *


class Example():
    """
    Stores text, tokens, and feature activations
    """

    def __init__(self, tokens, tokenizer, latent_data):
        # Make sure the latent data has the correct shape
        for hook_name, (latent_indices, latent_acts) in latent_data.items():
            assert len(latent_indices.shape) == 2 # N_pos x K
            assert len(latent_acts.shape) == 2    # N_pos x K
            assert len(tokens.shape) == 1         # N_pos
        
        self.tokens = tokens.tolist()
        self.str_tokens = [tokenizer.decode(token) for token in self.tokens]  
        self.text = "".join(self.str_tokens)     
        self.latent_data = latent_data
        
    def __str__(self):
        # Detokenize example
        return self.text

    def get_feature_set(self):
        # Get every single feature_id that activates on this example
        return set(self.latent_indices.flatten().tolist())
    
    def get_feature_activation(self, feature):
        # Get the activation of the specified feature on each token in this example
        # Get the feature activations on this example from the same layer as the passed in feature
        latent_indices, latent_acts = self.latent_data[feature.hook_name]
        feature_activations = torch.zeros(len(self.tokens), dtype=latent_acts.dtype)
        mask = latent_indices == feature.feature_id
        feature_activations[mask.any(dim=-1)] = latent_acts[mask]
        return feature_activations
    
    def get_tokens_feature_lists(self, feature):
        # Return tokens and feature activations
        return self.str_tokens, self.get_feature_activation(feature).tolist()
    
    def get_tokens_direction_scores(self, encoder, direction, hook_name):
        # Get the direction scores for this example
        # Normalize the direction
        direction = normalize_last_dim(direction)
        # Get the activations for this example
        activations = forward_pass_with_hooks(
            model=encoder.model,
            input_ids=torch.tensor(self.tokens, dtype=torch.long, device=encoder.model.device).unsqueeze(0),
            hook_points=[hook_name,]
        )[hook_name][0]
        # Get dot product between activations (n x d) and direction (d)
        scores = activations @ direction
        return self.str_tokens, scores.tolist()


class Feature():
    """
    Retrieves attributes of a SAE Feature
    """
    
    def __init__(self, feature_id, hook_name, db):
        self.feature_id = feature_id
        self.hook_name = hook_name
        self.db = db
        self.act_dist = None
        self.token_set = None

    def _load_feature_act_dist_from_db(self):
        # Load the distribution of the max activation of this feature on an example
        # over the entire database
        if self.act_dist is not None:
            # Check if we already got the feature activations to avoid redundant loading
            return self.act_dist
        else:
            self.db.assert_loaded()
            _, indices, values = self.db._get_tiv_parts(self.hook_name)
            feature_mask = indices == self.feature_id
            feature_activations = np.where(feature_mask, values, 0)
            max_activations = np.max(feature_activations, axis=(1, 2))
            self.act_dist = torch.from_numpy(max_activations).to(torch.float32)
            return self.act_dist
    
    def _load_feature_token_dist_from_db(self):
        # Get the set of unique tokens that this feature activates on
        # for over all the examples in the database
        if self.token_set is not None:
            # Check if we already got the token set to avoid redundant loading
            return self.token_set
        else:
            self.db.assert_loaded()
            tokens, indices, _ = self.db._get_tiv_parts(self.hook_name) 
            feature_mask = indices == self.feature_id
            active_mask = feature_mask.any(axis=2)
            active_tokens = tokens[active_mask]            
            self.token_set = np.unique(active_tokens)
            return self.token_set

    def get_max_activating(self, n):
        # Gets the top N max activating contexts in the dataset
        activation_dist = self._load_feature_act_dist_from_db()
        top_activating_examples = torch.topk(activation_dist, n).indices.tolist()
        return [self.db.load_example(example_id) for example_id in top_activating_examples]

    def get_num_nonzero(self):
        # Gets the top N max activating contexts in the dataset
        activation_dist = self._load_feature_act_dist_from_db()
        return (activation_dist > 0).sum().item()

    def get_quantiles(self, n_buckets, n):
        # Splits the dataset into n_buckets using quantiles, and returns n examples from each bucket
        activation_dist = self._load_feature_act_dist_from_db()
        quantiles = torch.linspace(0, 1, n_buckets + 1)
        thresholds = remove_duplicates(torch.quantile(activation_dist[activation_dist > 0], quantiles))
        result = {}
        for i in range(len(thresholds)-1):
            # Get indices of activations within the current quantile range
            if i == n_buckets - 1:
                bucket_indices = torch.where(activation_dist >= thresholds[i])[0]
                upper_bound = thresholds[i]  # Use the last threshold as upper bound
            else:
                bucket_indices = torch.where((activation_dist >= thresholds[i]) & (activation_dist < thresholds[i+1]))[0]
                upper_bound = thresholds[i+1]
            # Randomly select n indices from the bucket
            if len(bucket_indices) > n:
                selected_indices = bucket_indices[torch.randperm(len(bucket_indices))[:n]]
            else:
                selected_indices = bucket_indices            
            # Load the examples for the selected indices
            bucket_examples = [self.db.load_example(idx.item()) for idx in selected_indices]
            result[(thresholds[i].item(), upper_bound.item())] = bucket_examples
        return result

    def get_logits(self, n_logits=8):
        # Returns the most promoted and most suppressed tokens by this feature
        feature_dir = self.db.encoder.get_codebook(self.hook_name)[self.feature_id]        
        unembedding = self.db.encoder.model.lm_head.weight.to(torch.float32)
        feature_logits = unembedding @ feature_dir
        # Get top logits and indices
        top_values, top_indices = torch.topk(feature_logits, n_logits)
        top_tokens = [
            (self.db.encoder.tokenizer.decode([idx.item()]), value.item())
            for idx, value in zip(top_indices, top_values)
        ]
        # Get bottom logits and indices
        bottom_values, bottom_indices = torch.topk(feature_logits, n_logits, largest=False)
        bottom_tokens = [
            (self.db.encoder.tokenizer.decode([idx.item()]), value.item())
            for idx, value in zip(bottom_indices, bottom_values)
        ]
        return top_tokens, bottom_tokens