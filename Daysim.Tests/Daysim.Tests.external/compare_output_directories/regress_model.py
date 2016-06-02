
import argparse
import os
import filecmp
import sys
import traceback
import collections
import time
from enum import Enum
import shutil
import xml.etree.ElementTree as ET
import logging
import subprocess
import run_process_with_realtime_output
import utilities

def compare_directories(old_dir, new_dir):
    global args
    import compare_output_directories
    function_parameters = ['--outputs_reference', old_dir
                            ,'--outputs_new', new_dir
                            ]
    if args.verbose:
        function_parameters.append('-v')

    outputs_are_equal = compare_output_directories.are_outputs_equal_func(function_parameters)
    return outputs_are_equal 


class Const():
    REGRESSION_TEST_DIR = "regression_test_data"
    SHADOW_PRICES_FILENAME = "shadow_prices.txt"
    ARCHIVE_SHADOW_PRICES_FILENAME = "archive_" + SHADOW_PRICES_FILENAME
    PARK_AND_RIDE_SHADOW_PRICES_FILENAME = "park_and_ride_" + SHADOW_PRICES_FILENAME
    ARCHIVE_PARK_AND_RIDE_SHADOW_PRICES_FILENAME = "archive_" + PARK_AND_RIDE_SHADOW_PRICES_FILENAME

"""
It is a mistake for different configuration files to use the same working, estimation, or output directories
 so this keeps track of paths seen during any call (expected to be called multiple times from regress_subfolders.py)
"""
all_configured_changeable_directories = dict()

def parse_bool(v):
  return str(v[:1]).lower() in ("y", "t", "1")
    
def regress_model(parameters):
    """Passed a DaySim configuration file, this this renames the existing output directory, runs DaySim and compares the exisiting outputs directory to the new one using compare_output_directories.py"""
    start_time = time.perf_counter()
    script_directory = os.path.split(os.path.realpath(__file__))[0] + '/'
    parser = argparse.ArgumentParser(description='Run Daysim regression tests for specified model')
    parser.add_argument('--daysim_exe',
                        help='location of Daysim executable[default: %(default)s}', default= script_directory + '../../../Daysim/bin/x64/Debug/Daysim.exe')
    parser.add_argument('--configuration_file',
                        help='path to configuration file to send to Daysim', default='configuration_regression.xml')
    parser.add_argument('--run_if_needed_to_create_baseline',
                        help='if the output folder does not exist stting this to true will run it to create the baseline', default=True)
    parser.add_argument("-v", "--verbose", help="increase output verbosity",
                        action="store_true")

    global args
    args = parser.parse_args(parameters)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    if logging.getLogger().isEnabledFor(logging.DEBUG):
        print(args)
    
    logging.debug('Current working directory: ' + os.getcwd())
    logging.debug('script_directory: ' + script_directory)

    daysim_exe =  os.path.normpath(os.path.abspath(args.daysim_exe))
    logging.debug('daysim_exe: ' + daysim_exe)

    if not os.path.isfile(daysim_exe):
        raise Exception('daysim_exe is not a file: ' + daysim_exe)

    configuration_file = os.path.normpath(os.path.abspath(args.configuration_file))
    logging.debug('configuration_file: ' + configuration_file)
    if not os.path.isfile(configuration_file):
        raise Exception('configuration_file is not a file: ' + configuration_file)

    configuration_file_folder, configuration_filename = os.path.split(configuration_file)
    logging.debug('configuration_file_folder: ' + configuration_file_folder)

    #parse config file to know where output and working are being written to
    configuration_file_root, ext = os.path.splitext(configuration_filename)

    if ext != '.xml':
        raise Exception('configuration_file does not end in ".xml" so not yet supported in regression tests: ' + configuration_filename)

    tree = ET.parse(configuration_file)
    root = tree.getroot()
    
    configuration_base_path = root.get('BasePath')
    if configuration_base_path is None:
        configuration_base_path = configuration_file_folder
    else:
        configuration_base_path = os.path.normpath(os.path.join(configuration_file_folder, configuration_base_path))

    today_regression_results_dir = os.path.join(configuration_base_path, utilities.get_formatted_date() + '_regression_results')
    current_configuration_results_dir_name = utilities.get_formatted_time() + '_' + configuration_filename
    regression_results_dir = os.path.join(today_regression_results_dir, 'RUNNING_' + current_configuration_results_dir_name)

    os.makedirs(regression_results_dir)

    output_subpath = root.get('OutputSubpath')
    configured_output_path = os.path.normpath(os.path.join(configuration_base_path, output_subpath))
    logging.debug('configured_output_path: ' + configured_output_path)

    #compare the archived configuration file with the current one since this will find a very common error (different configuration) quickly
    archive_configuration_file_path = os.path.join(configured_output_path, 'archive_' +  configuration_filename)
    if not os.path.exists(archive_configuration_file_path):
        print('Skipping check for changed configuration file because "' + archive_configuration_file_path + '" does not exist in the output folder')
    else:
        if not filecmp.cmp(configuration_file, archive_configuration_file_path):
            raise Exception('configuration_file "' + configuration_file + '" different than archived configuration file in the output folder: ' + archive_configuration_file_path)
    outputs_new_basename = os.path.basename(configured_output_path)
    outputs_new_dir = os.path.join(regression_results_dir, outputs_new_basename)

    working_directory = root.get('WorkingDirectory')
    if working_directory is not None:
         raise Exception('configuration_file has WorkingDirectory which is deprecated and not supported for regression testing. Use WorkingSubpath instead')

    working_subpath = root.get('WorkingSubpath')
    configured_working_path = os.path.normpath(os.path.join(configuration_base_path, working_subpath))
    logging.debug('configured_working_path: ' + configured_working_path)

    working_new_basename = os.path.basename(configured_working_path)
    working_new_dir = os.path.join(regression_results_dir, working_new_basename)
    #create new regression test working directory in case need to store shadow price files inside
    os.makedirs(working_new_dir)

    estimation_subpath = root.get('EstimationSubpath')
    configured_estimation_path = os.path.normpath(os.path.join(configuration_base_path, estimation_subpath))
    logging.debug('configured_estimation_path: ' + configured_estimation_path)
    estimation_new_basename = os.path.basename(configured_estimation_path)
    estimation_new_dir = os.path.join(regression_results_dir, estimation_new_basename)

    working_new_basename = os.path.basename(configured_working_path)
    working_new_dir = os.path.join(regression_results_dir, working_new_basename)

    def check_all_configured_changeable_directories(parameter_value, parameter_type):
        #check that the working, output and estimation paths have not been seen
        if parameter_value in all_configured_changeable_directories:
            previous_configuration_file, parameter_type = all_configured_changeable_directories.get(parameter_value)
            raise Exception('Configuration file "' + configuration_file + '" specifies ' + parameter_type + ' which was used in a different configuration file: "' + previous_configuration_file + '" for ' + parameter_type)
        else:
            all_configured_changeable_directories[parameter_value] = (configuration_file, parameter_type)

    check_all_configured_changeable_directories(configured_output_path, 'output')
    check_all_configured_changeable_directories(configured_working_path, 'working')
    check_all_configured_changeable_directories(configured_estimation_path, 'estimation')

    #need to see if outputs folder archived shadow prices file exists and if so copy to the input location for shadow prices
    archived_shadow_prices_file_path = os.path.join(configured_output_path, Const.ARCHIVE_SHADOW_PRICES_FILENAME)
    if os.path.isfile(archived_shadow_prices_file_path):
        shutil.copyfile(archived_shadow_prices_file_path, os.path.join(working_new_dir, Const.SHADOW_PRICES_FILENAME))
    #repeat for Park and Ride Shadow Prices
    archived_park_and_ride_shadow_prices_file_path = os.path.join(configured_output_path, Const.ARCHIVE_PARK_AND_RIDE_SHADOW_PRICES_FILENAME)
    if os.path.isfile(archived_park_and_ride_shadow_prices_file_path):
        shutil.copyfile(archived_park_and_ride_shadow_prices_file_path, os.path.join(working_new_dir, Const.PARK_AND_RIDE_SHADOW_PRICES_FILENAME))

    override_parameters = [
                           'OutputSubpath=' + outputs_new_dir,
                           'WorkingSubpath=' + working_new_dir,
                           'EstimationSubpath=' + estimation_new_dir,
                          ]

    if not os.path.isdir(configured_output_path):
        if args.run_if_needed_to_create_baseline:
            print('configuration_file "' + configuration_file + '" specifies output subpath "' + output_subpath + '" which does not exist. --run_if_needed_to_create_baseline is true so will run now...')
            try:
                #due to bug Daysim needs to have the cwd be set to configuration_file dir https://github.com/RSGInc/Daysim/issues/52
                old_cwd = os.getcwd()
                os.chdir(configuration_file_folder)
                return_code = run_process_with_realtime_output.run_process_with_realtime_output(daysim_exe + ' --configuration "' + configuration_file + '"')
            finally:
                os.chdir(old_cwd)
        raise Exception('configuration_file "' + configuration_file + '" specifies output subpath "' + output_subpath + '" but that folder does not exist so cannot be used for regression.')
    
    try:
        #due to bug Daysim needs to have the cwd be set to configuration_file dir https://github.com/RSGInc/Daysim/issues/52
        old_cwd = os.getcwd()
        os.chdir(configuration_file_folder)

        return_code = run_process_with_realtime_output.run_process_with_realtime_output(daysim_exe + ' --configuration "' + configuration_file + '" --overrides="' + ','.join(override_parameters) + '"')
    finally:
        os.chdir(old_cwd)
    
    regression_passed = (return_code == 0) and \
                        compare_directories(configured_output_path, outputs_new_dir) and \
                        compare_directories(configured_working_path, working_new_dir) and \
                        compare_directories(configured_estimation_path, estimation_new_dir)

    results_label = 'PASSED' if regression_passed else 'FAILED'
    os.rename(regression_results_dir, os.path.join(today_regression_results_dir, results_label + '_' + current_configuration_results_dir_name))
    print('Regression test using configuration file "', configuration_filename, '": ' + results_label)
    return regression_passed
        
if __name__ == "__main__":
     try:
        model_regression_successful = regress_model(sys.argv[1:])
        sys.exit(0 if model_regression_successful else 1)
     except Exception as ex:
        print("Exception in user code:")
        print("-"*60)
        traceback.print_exc(file=sys.stdout)
        print("-"*60)
        sys.exit(ex)

