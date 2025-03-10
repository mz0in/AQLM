from __future__ import annotations

import warnings
from argparse import Namespace
from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, Iterator, List, Sequence, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.parallel.scatter_gather import Gather

from aq_engine import replace_parameter_
from src.utils import iterate_minibatches


@torch.enable_grad()
def finetune_groupwise(
    *,
    layer: nn.Module,
    inps: Sequence[torch.Tensor],
    outs: Sequence[torch.Tensor],
    args: Namespace,
    verbose: bool = True,
    **kwargs,
) -> nn.Module:
    """
    Fine-tune a module with pre-quantized linear layers so as to minimize MSE between layer-wise inps/outs

    :param layer: a trainable module where linear layers are replaced by QuantizedLinear instances
    :param inps: a list of tensors of input activations, [nsamples_per_device, seq_len, hidden_size]
    :param outs: a list of tensors of previous output activations, [nsamples_per_device, seq_len, hidden_size]
    :param args: quantization hyperparameters from main.py
    :param kwargs: additional keyword arguments to be passed into layer on each forward
    """
    assert isinstance(args.devices, (list, tuple)) and len(args.devices) >= 1, f"Found devices = {args.devices}"
    assert isinstance(inps, (list, tuple)) and isinstance(inps, (list, tuple))
    assert len(inps) == len(outs) == len(args.devices)
    for i in range(len(args.devices)):
        assert isinstance(inps[i], torch.Tensor) and isinstance(outs[i], torch.Tensor)
        if not args.offload_activations:
            assert inps[i].device == outs[i].device == args.devices[i], (inps[i].device, outs[i].device, args.devices)
        else:
            assert inps[i].device == outs[i].device == torch.device("cpu")
            assert inps[i].is_pinned() and outs[i].is_pinned()

    # replicate non-trainable parameters to each GPU
    replicas = kwargs_by_device = None
    if len(args.devices) > 1:
        replicas = torch.nn.parallel.replicate(layer, args.devices)
        replicas[0] = layer
        kwargs_by_device = []
        for device in args.devices:
            kwargs_by_device.append(
                {k: (v.to(device, non_blocking=True) if isinstance(v, torch.Tensor) else v) for k, v in kwargs.items()}
            )

    # initialize trainable parameters on main device; prepare to send them to replicas
    differentiable_parameters_by_name = {name: param for name, param in layer.named_parameters() if param.requires_grad}
    param_names, differentiable_parameters = zip(*differentiable_parameters_by_name.items())
    differentiable_parameters = nn.ParameterList(differentiable_parameters)
    if replicas:
        replacement_tables = _make_parameter_replacement_tables(layer, replicas, param_names, differentiable_parameters)

    print(f"Fine-tuning {sum(param.numel() for param in differentiable_parameters)} parameters")
    opt = torch.optim.Adam(
        differentiable_parameters, lr=args.finetune_lr, betas=(args.finetune_adam_beta1, args.finetune_adam_beta2)
    )

    # backup best parameters
    if args.finetune_keep_best:
        best_parameters = deepcopy(differentiable_parameters)

    assert args.finetune_batch_size % len(args.devices) == 0, "batch_size must be divisible by the number of GPUs"

    num_samples_per_device = len(inps[0])
    local_batch_size = args.local_batch_size
    if local_batch_size is None:
        local_batch_size = args.finetune_batch_size // len(args.devices)

    assert all(len(inps_tensor) == num_samples_per_device for inps_tensor in inps)
    assert args.finetune_batch_size % (local_batch_size * len(args.devices)) == 0, ""
    num_accumulation_steps = args.finetune_batch_size // (local_batch_size * len(args.devices))
    assert num_samples_per_device % local_batch_size * num_accumulation_steps == 0, (
        num_samples_per_device,
        local_batch_size,
    )
    steps_per_epoch = num_samples_per_device * len(args.devices) // args.finetune_batch_size
    batch_iterators = [
        iterate_minibatches(inps[i], outs[i], batch_size=local_batch_size, device=args.devices[i])
        for i in range(len(args.devices))
    ]

    previous_best_loss = float("inf")  # for early stopping
    steps_accumulated = 0
    for epoch in range(args.finetune_max_epochs):
        loss_numerator = loss_denominator = 0
        for step in range(steps_per_epoch):
            if len(args.devices) == 1:
                loss = _compute_mse_on_batch(layer, batch_iterators[0], **kwargs)
            else:
                loss = _compute_mse_parallel(
                    args.devices,
                    replicas,
                    differentiable_parameters,
                    replacement_tables,
                    batch_iterators,
                    kwargs_by_device,
                )

            (loss / num_accumulation_steps).backward()
            steps_accumulated += 1

            if not torch.isfinite(loss).item():
                raise ValueError(f"Fine-tuning loss is {loss}")
            if steps_accumulated >= num_accumulation_steps:
                opt.step()
                opt.zero_grad()
                steps_accumulated = 0

            loss_numerator += loss.item()
            loss_denominator += 1
            if verbose and (epoch * steps_per_epoch + step) % args.print_frequency == 0:
                print(f"epoch={epoch}\tstep={step}\tloss={loss_numerator / loss_denominator:.10f}\t")

        if verbose and (epoch * steps_per_epoch + step) % args.print_frequency != 0:
            print(f"epoch={epoch}\tstep={step}\tloss={loss_numerator / loss_denominator:.10f}\t")

        if args.finetune_relative_mse_tolerance is not None:
            epoch_loss = loss_numerator / loss_denominator
            if args.finetune_keep_best:
                if epoch_loss / previous_best_loss < 1.0:
                    best_parameters = deepcopy(differentiable_parameters)
                else:
                    differentiable_parameters = best_parameters
            if epoch_loss / previous_best_loss > (1.0 - args.finetune_relative_mse_tolerance):
                return layer  # early stopping; no updates after last epoch's beam search
            previous_best_loss = min(epoch_loss, previous_best_loss)

    return layer


def _make_parameter_replacement_tables(
    layer: nn.Module, replicas: Sequence[nn.Module], param_names: Sequence[str], parameters: nn.ParameterList
) -> Sequence[List[Sequence[Tuple[nn.Module, str]]]]:
    """
    Prepare auxiliary data structures for quickly copying parameters to replicas for data-parallel training.

    """
    assert len(param_names) == len(parameters)
    assert len(replicas) > 1
    assert replicas[0] is layer

    parameters_by_name = dict(zip(param_names, parameters))

    param_to_name = {param: name for name, param in parameters_by_name.items()}
    param_occurences = defaultdict(list)  # param_name -> List [ Tuple [submodule name, attr name] ]
    for submodule_name, submodule in layer.named_modules():
        for attr_name, param in submodule.named_parameters(recurse=False):  # immediate params (excluding children)
            if param in param_to_name:
                param_name = param_to_name[param]
                param_occurences[param_name].append((submodule_name, attr_name))
    assert len(param_occurences) == len(parameters), "internal error: not all parameters were found"

    replacement_tables = []
    for replica in replicas:
        replacement_table = list()  # for each master param -> List[ Tuple[replica submodule, attr name] ]
        replica_modules_by_name: Dict[str, nn.Module] = dict(replica.named_modules())

        for param_name, master_param in zip(param_names, parameters):
            param_replacements = list()
            for submodule_name, attr_name in param_occurences[param_name]:
                param_replacements.append((replica_modules_by_name[submodule_name], attr_name))
            replacement_table.append(param_replacements)
        replacement_tables.append(replacement_table)
    return replacement_tables


def _compute_mse_on_batch(
    layer: nn.Module, batch_iter: Iterator[Tuple[torch.Tensor, torch.Tensor]], **kwargs
) -> torch.Tensor:
    """
    Compute the activation MSE error between transformer layers
    :param
    """
    inps_batch, outs_batch = next(batch_iter)
    inps_batch = inps_batch.to(dtype=torch.float32)
    outs_batch = outs_batch.to(dtype=torch.float32)

    if inps_batch.shape[0] != 1:  # replicate kwargs to match the batch size
        for name, value in list(kwargs.items()):
            if isinstance(value, torch.Tensor) and value.shape[0] == 1:
                if name not in ("attention_mask", "position_ids"):
                    warnings.warn(f"Tiling an unexpected kwarg {name} over batch size; make sure this is valid.")
                repeats = [len(inps_batch)] + [1 for _ in range(value.ndim - 1)]
                kwargs[name] = value.tile(*repeats)

    outs_prediction, *_unused = layer(inps_batch, **kwargs)
    assert outs_prediction.shape == outs_batch.shape
    return F.mse_loss(outs_prediction, outs_batch)


def _compute_mse_parallel(
    devices: Sequence[torch.device],
    replicas: Sequence[nn.Module],
    parameters_to_replicate: nn.ParameterList,
    replacement_tables: Sequence[List[Sequence[Tuple[nn.Module, str]]]],
    batch_iterators: Sequence[Iterator[Tuple[torch.Tensor, torch.Tensor]]],
    kwargs_by_device: Sequence[Dict[str, Any]],
) -> torch.Tensor:
    """Compute MSE in parallel over multiple GPUs, each GPU processes a portion of samples"""
    replicated_parameters = torch.nn.parallel.replicate(parameters_to_replicate, devices, detach=False)
    funcs_by_replica = [_compute_mse_on_batch for _ in replicas]
    inputs_by_replica = []
    for i in range(len(devices)):
        if i != 0:  # no overrides needed for master module
            for replacement_param, replacement_table in zip(replicated_parameters[i], replacement_tables[i]):
                for (replica_submodule, attr_name) in replacement_table:
                    replace_parameter_(replica_submodule, attr_name, replacement_param)
        inputs_by_replica.append((replicas[i], batch_iterators[i]))
    mse_components = torch.nn.parallel.parallel_apply(
        funcs_by_replica, inputs_by_replica, kwargs_by_device, devices=devices
    )
    return Gather.apply(devices[0], 0, *(mse.view(1) for mse in mse_components)).mean()
