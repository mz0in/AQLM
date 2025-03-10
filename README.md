# AQLM

Official PyTorch implementation for [Extreme Compression of Large Language Models via Additive Quantization](https://arxiv.org/pdf/2401.06118.pdf)

## Inference

### Demo

Learn how to run the prequantized models using this Google Colab examples:

Running `Mixtral` on a single T4 GPU:

<a target="_blank" href="https://colab.research.google.com/github/Vahe1994/AQLM/blob/main/notebooks/colab_example.ipynb">
  <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="AQLM In Colab"/>
</a>

Streaming with GPU/CPU:

<a target="_blank" href="https://colab.research.google.com/github/Vahe1994/AQLM/blob/main/notebooks/streaming_example.ipynb">
  <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="AQLM In Colab"/>
</a>

### Models

This repository is currently designed to work with models of `LLaMA`, `Mistral` and `Mixtral` families.

We provide a number of prequantized models:

| Model      | AQLM scheme | WikiText 2 PPL | Model size, Gb | Hub link                                                                 |
|------------|-------------|----------------|----------------|--------------------------------------------------------------------------|
| Llama-2-7b | 1x16        | 6.31           | 2.4            | [Link](https://huggingface.co/BlackSamorez/Llama-2-7b-AQLM-2Bit-1x16-hf) |
| Llama-2-7b | 2x8         | 7.98           | 2.2            | [Link](https://huggingface.co/BlackSamorez/Llama-2-7b-AQLM-2Bit-2x8-hf)  |
| Llama-2-7b | 8x8         | 7.83           | 2.2            | [Link](https://huggingface.co/BlackSamorez/Llama-2-7b-AQLM-2Bit-8x8-hf)  |
| Llama-2-13b| 1x16        | 5.41           | 4.1            | [Link](https://huggingface.co/BlackSamorez/Llama-2-13b-AQLM-2Bit-1x16-hf)|
| Llama-2-70b| 1x16        | 3.96           | 18.8           | [Link](https://huggingface.co/BlackSamorez/Llama-2-70b-AQLM-2Bit-1x16-hf)|
| Llama-2-70b| 2x8         | 4.83           | 18.2           | [Link](https://huggingface.co/BlackSamorez/Llama-2-70b-AQLM-2Bit-2x8-hf) |
| Mixtral-8x7b| 1x16       | 4.37           | 12.6            | [Link](https://huggingface.co/BlackSamorez/Mixtral-8x7b-AQLM-2Bit-1x16-hf)|


### Inference kernels

AQLM quantization setpus vary mainly on the number of codebooks used as well as the codebook sizes in bits. The most popular setups, as well as inference kernels they support are:
 
| Kernel | Number of codebooks | Codebook size, bits | Scheme Notation | Accuracy | Speedup     | Fast GPU inference | Fast CPU inference |
|---|---------------------|---------------------|----------|-------------|-------------|--------------------|--------------------|
| Triton | K                   | N                  | KxN     | -        | Up to ~0.7x | ✅                  | ❌                  |
| CUDA | 1                   | 16                  | 1x16     | Best        | Up to ~1.3x | ✅                  | ❌                  |
| CUDA | 2                   | 8                   | 2x8      | OK          | Up to ~3.0x | ✅                  | ❌                  |
| Numba | K                   | 8                   | Kx8      | Good        | Up to ~4.0x | ❌                  | ✅                  |

### Installation



To run the models, one would have to install an inference library:
```bash
pip install aqlm[gpu,cpu]
```
, specifying either `gpu`, `cpu` or both based on one's inference setting.


Then, one can use the familiar `.from_pretrained` method provided by the [transformers](https://github.com/huggingface/transformers) library:
```python
from transformers import AutoModelForCausalLM

quantized_model = AutoModelForCausalLM.from_pretrained(
    "BlackSamorez/Llama-2-7b-AQLM-2Bit-1x16-hf",
    trust_remote_code=True, torch_dtype="auto"
).cuda()
```
Notice that `torch_dtype` should be set to either `torch.float16` or `"auto"` on GPU and `torch.float32` on CPU. After that, the model can be used exactly the same as one would use and unquantized model. 



## Quantization

### Dependencies

Install packages from `requirements.txt`:
```bash
pip install -r requirements.txt
```

### Loading / caching datasets and tokenizer

The script will require downloading and caching locally the relevant tokenizer and the datasets. 
They will be saved in default Huggingface Datasets directory unless alternative location is provided by env variables.
See [relevant Datasets documentation section](https://huggingface.co/docs/datasets/main/en/cache#cache-directory)

### Data

When quantizing models with AQLM, we recommend that you use a subset of the original data the model was trained on.

For Llama-2 models, the closest available dataset is [RedPajama](https://huggingface.co/datasets/togethercomputer/RedPajama-Data-1T-Sample) . To load subset of RedPajama provide "pajama" in --dataset argument.
This will process nsamples data and tokenize it using provided model tokenizer.

Additionally we provide tokenized Redpajama for LLama and Solar/Mistral models for 4096 context lengths stored in [Hunggingface](https://huggingface.co/datasets/Vahe1994/AQLM) .
To load it, use:

```python
from huggingface_hub import hf_hub_download

hf_hub_download(repo_id="Vahe1994/AQLM", filename="data/name.pth",repo_type="dataset")
```

To use downloaded data from HF, place it in data folder(optional) and set correct path to it in "--dataset" argument in main.py.

**Warning:** These subsets are already processed with the corresponding model tokenizer. If you want to quantize another model (e.g. mistral/mixtral), please re-tokenize the data with provided script in src/datautils.

### WandB logging

One can optionally log the data to `Weights and Biases` service (wandb).
Run `pip install wandb` for W&B logging.
Specify `$WANDB_ENTITY`, `$WANDB_PROJECT`, `$WANDB_NAME` environment variables prior to running experiments. use `--wandb` argument to enable logging

### GPU and RAM requirements
This code was developed and tested using a several A100 GPU with 80GB GPU RAM. 
You can use the `--offload activations` option to reduce VRAM usage.
For `Language Model Evaluation Harness` evaluation one needs to have enough memory to load whole model  + activation tensors 
on one or several devices.

### Model downloading
The code requires the LLaMA model to be downloaded in Huggingface format and saved locally. The scripts below assume that `$TRANSFORMERS_CACHE` variable points to the Huggingface Transformers cache folder.
To download and cache the models, run this in the same environment:

```python
from transformers import AutoTokenizer, AutoModelForCausalLM
model_name = "meta-llama/Llama-2-7b-hf"  # or whatever else you wish to download
tokenizer = AutoTokenizer.from_pretrained(model_name, torch_dtype="auto")
model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype="auto")
```


### How to quantize a model with AQLM
This script compresses the model and then tests its performance in terms of perplexity using WikiText2, C4, and Penn Treebank datasets. 

The command to launch the script should look like this: 

```bash
export CUDA_VISIBLE_DEVICES=0   # or e.g. 0,1,2,3
export MODEL_PATH=<PATH_TO_MODEL_ON_HUB>
export DATASET_PATH=<INSERT DATASET NAME OR PATH TO CUSTOM DATA>
export SAVE_PATH=/path/to/save/quantized/model/
export WANDB_PROJECT=MY_AQ_EXPS
export WANDB_NAME=COOL_EXP_NAME

python main.py $MODEL_PATH $DATASET_PATH --nsamples=1024 \
 --num_codebooks=1 --nbits_per_codebook=16 --in_group_size=8 \
 --relative_mse_tolerance=0.01 --finetune_relative_mse_tolerance=0.001 \
 --finetune_batch_size=32 --local_batch_size=1 --offload_activations \
 --wandb --save $SAVE_PATH

```

Main CLI arguments:
- `CUDA_VISIBLE_DEVICES` - by default, the code will use all available GPUs. If you want to use specific GPUs (or one GPU), use this variable.
- `MODEL_PATH` - a path to either hugginface hub (e.g. meta-llama/Llama-2-7b-hf) or a local folder with transformers model and a tokenizer.
- `DATASET_PATH` - either a path to calibration data (see above) or a standard dataset `[c4, ptb, wikitext2]`
   - for llama-2 models, you can use `DATASET_PATH=./data/red_pajama_n=1024_4096_context_length.pth` for a slice of RedPajama (up to 1024 samples)
- `--nsamples` - the number of calibration data _sequences_. If this parameter is not set, take all calibration data avaialble.
- `--num_codebooks` - number of codebooks per layer
- `--nbits_per_codebook` - each codebook will contain 2 ** nbits_per_codebook vectors
- `--in_group_size` - how many weights are quantized together (aka "g" in the arXiv paper)
- `--finetune_batch_size` - (for fine-tuning only) the total number of sequences used for each optimization step
- `--local_batch_size` - when accumulating finetune_batch_size, process this many samples per GPU per forward pass (affects GPU RAM usage)
- `--relative_mse_tolerance`- (for initial calibration) - stop training when (current_epoch_mse / previous_epoch_mse) > (1 - relative_mse_tolerance)
- `--finetune_relative_mse_tolerance`- same, but for fine-tuning
- `--offload_activations` -- during calibration, move activations from GPU memory to RAM. This reduces VRAM usage while slowing calibration by ~10% (depending on your hardware). 
- `--save` -- path to save/load quantized model. (see also: `--load`)
- `--wandb` - if this parameter is set, the code will log results to wandb

There are additional hyperparameters aviailable. Run `python main.py --help` for more details on command line arguments, including compression parameters.

### Zero-shot benchmarks via LM Evaluation Harness

To perform zero-shot evaluation, we use [Language Model Evaluation Harness](https://github.com/EleutherAI/lm-evaluation-harness) framework with slight modifications. This repository contains a copy of LM Evaluation Harness repo from early 2023 in `lm-eval-harness` folder. 

Before running the code make sure that you have all the requirements and dependencies of `lm-eval-harness` installed. To install them run:
```
pip install -r lm-evaluation-harness/requirements.txt
```

The main script launching the evaluation procedure is `lmeval.py` .


```bash
export CUDA_VISIBLE_DEVICES=0,1,2,3  # optional: select GPUs
export QUANTZED_MODEL=<PATH_TO_SAVED_QUANTIZED_MODEL_FROM_MAIN.py>
export MODEL_PATH=<INSERT_PATH_TO_ORIINAL_MODEL_ON_HUB>
export DATASET=<INSERT DATASET NAME OR PATH TO CUSTOM DATA>
export WANDB_PROJECT=MY_AQ_LM_EVAL
export WANDB_NAME=COOL_EVAL_NAME

python lmeval.py \
    --model hf-causal \
    --model_args pretrained=$MODEL_PATH,dtype=float16,use_accelerate=True \
    --load $QUANTZED_MODEL \
    --tasks winogrande,piqa,hellaswag,arc_easy,arc_challenge \
    --batch_size 1
```

### Preparing models for inference

To convert a model into a _Hugging Face_ compatible format, use `convert_to_hf.py` with corresponding arguments:
 - `--model` - the original pretrained model (corresponds to `MODEL_PATH` of `main.py`, e.g. `meta-llama/Llama-2-7b-hf`).
 - `--in_path` - the folder containing an initially quantized model (corresponds to `--save` of `main.py`).
 - `--out_path` - the folder to save `transformers` model to.

The conversion automatically

## Contributing

If you want to contribute something substantial (more than a typo), please open an issue first.
We use black and isort for all pull requests. Before committing your code run `black . && isort .`

## Cite

If you found this work useful, please consider citing:

```
@misc{egiazarian2024extreme,
      title={Extreme Compression of Large Language Models via Additive Quantization}, 
      author={Vage Egiazarian and Andrei Panferov and Denis Kuznedelev and Elias Frantar and Artem Babenko and Dan Alistarh},
      year={2024},
      eprint={2401.06118},
      archivePrefix={arXiv},
      primaryClass={cs.LG}
}
```
