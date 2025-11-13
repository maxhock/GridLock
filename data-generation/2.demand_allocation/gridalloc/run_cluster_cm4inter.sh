#!/bin/bash
#SBATCH -J test_run
#SBATCH --output=logs/normal/%j_output.log
#SBATCH --error=logs/errors/%j_error.log

#SBATCH --clusters=inter
#SBATCH --partition=cm4_inter
#SBATCH --nodes=1
#SBATCH --ntasks=7             
#SBATCH --cpus-per-task=16
#SBATCH --time=0-04:30:00
#SBATCH --mem-per-cpu=4G

### Load modules and python
module load miniconda3/24.7.1
module list

eval "$(conda shell.bash hook)"
conda activate grid_alloc
conda env list


### Script Info
start_time=$(date +"%Y-%m-%d %H:%M:%S")
echo "Script started at: $start_time"

INDEX="$1"
echo "Fileindex: $INDEX"


for RID in $(seq 0 6); do
  GLOBAL_ID=$(( INDEX + RID ))
  srun --exclusive \
       --ntasks=1 \
       --cpus-per-task=16 \
       python3 main.py $GLOBAL_ID --n_cpu 16 &
done
wait


### Delete error log file at end of run if it is empty
ERR_FILE="logs/errors/${SLURM_JOB_ID}_error.log"

if [ -f "$ERR_FILE" ] && [ ! -s "$ERR_FILE" ]; then
  rm "$ERR_FILE"
fi

echo "Done!!!"