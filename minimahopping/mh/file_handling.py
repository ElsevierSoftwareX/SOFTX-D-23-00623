import os
import warnings

def restart(outpath, restart_path, minima_path, is_master):
        
    if not os.path.exists(outpath):
        os.mkdir(outpath)
        is_output = False
    else:
        is_output = True
        
    if not os.path.exists(restart_path):
        os.mkdir(restart_path)
        is_restart = False
    else:
        is_restart = True

    if not os.path.exists(minima_path):
        os.mkdir(minima_path)

    if is_restart and is_output:
        is_restart = True
        is_files = checkfiles(restart_path, is_master)
        if not is_files:
            is_restart = False
    elif is_output and not is_restart:
        msg = "Preveous run detected but no restart files. New run is started."
        warnings.warn(msg, UserWarning)
        is_restart = False
    else:
        is_restart = False
    
    return is_restart


def checkfiles(restart_path, is_master):
    is_files = True
    if is_master:
        is_database = os.path.exists(restart_path + "minima.pickle.shelve.dat") or os.path.exists(restart_path + "minima.pickle.shelve.dat.dat")
        if not is_database:
            msg = "No database file detected. New MH run is started " + restart_path + "minima.pickle.shelve.dat"
            warnings.warn(msg, UserWarning)
            is_files = False
    
    if not is_master:
        is_minimum = os.path.exists(restart_path + "poscur.extxyz")
        if not is_minimum:
            msg = "No current minimum detected. New MH run is started " +  restart_path + "poscur.extxyz"
            warnings.warn(msg, UserWarning)
            is_files = False

    is_params = os.path.exists(restart_path + "params.json")
    if not is_params:
        msg = "No current minimum detected. New MH run is started " + restart_path + "params.json"
        warnings.warn(msg, UserWarning)
        is_files = False

    return is_files

