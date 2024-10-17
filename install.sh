#!/bin/bash

# Check if conda is installed
if ! command -v conda &> /dev/null
then
    echo "conda could not be found. Please install Miniconda or Anaconda."
    exit
fi

# Create the conda environment if it doesn't exist
if ! conda info --envs | grep -q "slack_exporter"; then
    echo "Creating conda environment..."
    conda env create -f environment.yml
else
    echo "Conda environment 'slack_exporter' already exists."
fi

# Activate the conda environment
echo "Activating the conda environment..."
conda activate slack_exporter

echo "Setup is complete. Run the script with the conda environment activated."
echo "To activate the environment: conda activate slack_exporter"
