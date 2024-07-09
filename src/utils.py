import torch
from tqdm.auto import tqdm



class Feature():
    
    # Stores top examples
        # We can have examples for each quartile
    # Stores range of activations
    # Stores other statistics
    pass

class Example():
    
    # Stores text
    # Stores tokens for an example
    # Stores feature activations at each position   
    pass



class AggregatedFeatureActivations:
    """
    Gives the max activating data examples for each firing feature
    and computes other feature astat
    """
    
    # Stores all features 
    
    def __init__(
        
    ):
        pass
        
        

@torch.inference_mode()
def featurize(
    model,
    encoder_dict,
    tokens,
):
    """
    Returns two B x P x k tensor of feature activations
    Nonactivating features will be a zero
    """
    # Initialize output tensor
    n_batch, n_pos = tokens.shape
    feat_activations = {
        idx: [
                torch.zeros((n_batch, n_pos, encoder_dict[idx].cfg.k), dtype=torch.int64),
                torch.zeros((n_batch, n_pos, encoder_dict[idx].cfg.k), dtype=torch.float32),                
        ] for idx in encoder_dict
    }
    # Calculate SAE features
    with torch.no_grad():
        # Do a foward pass to get model acts
        model_acts = model(
            input_ids=tokens.to(model.device), output_hidden_states=True
        ).hidden_states[:-1]
        # Calculate the SAE features
        for idx in encoder_dict:
            encoder = encoder_dict[idx]
            model_acts_layer = model_acts[idx]
            encoder_output = encoder.encode(model_acts_layer.flatten(0, 1))
            feat_activations[idx][0] = encoder_output.top_indices.reshape(n_batch, n_pos, -1).cpu()
            feat_activations[idx][1] = encoder_output.top_acts.reshape(n_batch, n_pos, -1).cpu()
    return feat_activations


@torch.inference_mode()    
def batched_featurize(
    model,
    encoder,
    tokens,
    batch_size,
):
    """
    Batched version of featurize
    """
    minibatches = tokens.split(batch_size)
    feat_activations = featurize(model, encoder, minibatches[0])
    for minibatch in tqdm(minibatches[1:]):
        new_feat_activations = expanded_featurize(model, encoder, minibatch)
        feat_activations = {idx: [
            torch.cat((feat_activations[idx][0], new_feat_activations[idx][0]), dim=0),
            torch.cat((feat_activations[idx][1], new_feat_activations[idx][1]), dim=0)
        ] for idx in feat_activations}
    return feat_activations


@torch.inference_mode()
def expanded_featurize(
    model,
    encoder_dict,
    tokens,
    f_idx=None,
):
    """
    Returns a B x P x n_feats tensor of feature activations
    Nonactivating features will be a zero
    REALLY SLOW AND MEMORY INTENSIVE
    """
    # Initialize output tensor
    n_batch, n_pos = tokens.shape
    feat_activations = {
        idx: torch.zeros((n_batch, n_pos, encoder_dict[idx].encoder.out_features), dtype=torch.float32)
        for idx in encoder_dict
    }
    # Calculate SAE features
    with torch.no_grad():
        # Do a foward pass to get model acts
        model_acts = model(
            input_ids=tokens.to(model.device), output_hidden_states=True
        ).hidden_states[:-1]
        # Calculate the SAE features
        for idx in encoder_dict:
            encoder = encoder_dict[idx]
            model_acts_layer = model_acts[idx]
            encoder_output = encoder.encode(model_acts_layer.flatten(0, 1))
            encoder_output_indices = encoder_output.top_indices.reshape(n_batch, n_pos, -1).cpu()
            encoder_output_acts = encoder_output.top_acts.reshape(n_batch, n_pos, -1).cpu()
            feat_activations[idx].scatter_(-1, encoder_output_indices, encoder_output_acts)
    # If we pass in feature list, return just those features
    if f_idx != None:
        if isinstance(f_idx, int):
            f_idx = [f_idx]
        assert isinstance(f_idx, list)
        assert isinstance(f_idx[0], int)
        for idx in feat_activations:
            feat_activations[idx] = feat_activations[:, :, f_idx]
    return feat_activations


@torch.inference_mode()    
def batched_expanded_featurize(
    model,
    encoder,
    tokens,
    batch_size,
    f_idx=None,
):
    """
    Batched version of expanded_featurize
    REALLY SLOW AND MEMORY INTENSIVE
    """
    minibatches = tokens.split(batch_size)
    feat_activations = expanded_featurize(model, encoder, minibatches[0], f_idx)
    for minibatch in tqdm(minibatches[1:]):
        new_feat_activations = expanded_featurize(model, encoder, minibatch, f_idx)
        feat_activations = {idx: torch.cat((feat_activations[idx], new_feat_activations[idx]), dim=0)
                            for idx in feat_activations}
    return feat_activations