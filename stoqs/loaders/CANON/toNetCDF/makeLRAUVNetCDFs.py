#!/usr/bin/env python
__author__    = 'Danelel Cline'
__copyright__ = '2016'
__license__   = 'GPL v3'
__contact__   = 'dcline at mbari.org'

__doc__ = '''

Creates interpolated netCDF files for all LRAUV data; engineering and science data

Execute from cron on kraken like:
docker-compose run -u 1087 -T -v /dev/shm:/dev/shm -v /tmp:/tmp -v /mbari/LRAUV:/mbari/LRAUV stoqs stoqs/loaders/CANON/toNetCDF/makeLRAUVNetCDFs.py --trackingdb --nudge --start 20120901 --end 20121001

To debug:
docker-compose run -u 1087 --rm -v /dev/shm:/dev/shm -v /tmp:/tmp -v /mbari/LRAUV:/mbari/LRAUV stoqs stoqs/loaders/CANON/toNetCDF/makeLRAUVNetCDFs.py --trackingdb --nudge --start 20120901 --end 20121001

'''

import os
import sys
import logging
import re
import pydap
import json
import netCDF4
import lrauvNc4ToNetcdf
import requests

from coards import to_udunits, from_udunits
from thredds_crawler.crawl import Crawl
from urllib.parse import urlparse
from datetime import datetime
from loaders.LRAUV.make_load_scripts import lrauvs

# Set up global variables for logging output to STDOUT

SCI_PARMS = {'Aanderaa_O2': [{'name': 'mass_concentration_of_oxygen_in_sea_water',
                              'rename': 'oxygen'}],
             'CTD_NeilBrown': [{'name': 'sea_water_salinity', 'rename': 'salinity'},
                               {'name': 'sea_water_temperature', 'rename': 'temperature'}],
             'CTD_Seabird': [{'name': 'sea_water_salinity', 'rename': 'salinity'},
                             {'name': 'sea_water_temperature', 'rename': 'temperature'}],
             'ISUS': [{'name': 'mole_concentration_of_nitrate_in_sea_water',
                       'rename': 'nitrate'}],
             'PAR_Licor': [{'name': 'downwelling_photosynthetic_photon_flux_in_sea_water',
                            'rename': 'PAR'}],
             'WetLabsBB2FL': [{'name': 'mass_concentration_of_chlorophyll_in_sea_water',
                               'rename': 'chlorophyll'},
                              {'name': 'Output470', 'rename': 'bbp470'},
                              {'name': 'Output650', 'rename': 'bbp650'}],
             'WetLabsSeaOWL_UV_A': [{'name': 'concentration_of_chromophoric_dissolved_organic_matter_in_sea_water',
                                     'rename': 'chromophoric_dissolved_organic_matter'},
                                    {'name': 'mass_concentration_of_chlorophyll_in_sea_water',
                                     'rename': 'chlorophyll'},
                                    {'name': 'BackscatteringCoeff700nm',
                                     'rename': 'BackscatteringCoeff700nm'},
                                    {'name': 'VolumeScatCoeff117deg700nm',
                                     'rename': 'VolumeScatCoeff117deg700nm'},
                                    {'name': 'mass_concentration_of_petroleum_hydrocarbons_in_sea_water',
                                     'rename': 'petroleum_hydrocarbons'}]}

ENG_PARMS = {'BPC1': [{'name': 'platform_battery_charge',
                       'rename': 'health_platform_battery_charge'},
                      {'name': 'platform_battery_voltage',
                       'rename': 'health_platform_average_voltage'}],
             'BuoyancyServo': [{'name': 'platform_buoyancy_position',
                                'rename': 'control_inputs_buoyancy_position'}],
             'DeadReckonUsingMultipleVelocitySources': [{'name': 'fix_residual_percent_distance_traveled',
                                                         'rename': 'fix_residual_percent_distance_traveled_DeadReckonUsingMultipleVelocitySources'},
                                                        {'name': 'longitude',
                                                         'rename': 'pose_longitude_DeadReckonUsingMultipleVelocitySources'},
                                                        {'name': 'latitude',
                                                         'rename': 'pose_latitude_DeadReckonUsingMultipleVelocitySources'},
                                                        {'name': 'depth',
                                                         'rename': 'pose_depth_DeadReckonUsingMultipleVelocitySources'}],
             'DeadReckonUsingSpeedCalculator': [{'name': 'fix_residual_percent_distance_traveled',
                                                 'rename': 'fix_residual_percent_distance_traveled_DeadReckonUsingSpeedCalculator'},
                                                {'name': 'longitude',
                                                 'rename': 'pose_longitude_DeadReckonUsingSpeedCalculator'},
                                                {'name': 'latitude',
                                                 'rename': 'pose_latitude_DeadReckonUsingSpeedCalculator'},
                                                {'name': 'depth',
                                                 'rename': 'pose_depth_DeadReckonUsingSpeedCalculator'}],
             'ElevatorServo': [{'name': 'platform_elevator_angle',
                                'rename': 'control_inputs_elevator_angle'}],
             'MassServo': [{'name': 'platform_mass_position',
                            'rename': 'control_inputs_mass_position'}],
             'NAL9602': [{'name': 'time_fix', 'rename': 'fix_time'},
                         {'name': 'latitude_fix', 'rename': 'fix_latitude'},
                         {'name': 'longitude_fix', 'rename': 'fix_longitude'}],
             'Onboard': [{'name': 'platform_average_current',
                          'rename': 'health_platform_average_current'}],
             'RudderServo': [{'name': 'platform_rudder_angle',
                              'rename': 'control_inputs_rudder_angle'}],
             'ThrusterServo': [{'name': 'platform_propeller_rotation_rate',
                                'rename': 'control_inputs_propeller_rotation_rate'}]}

SCIENG_PARMS = {**SCI_PARMS, **ENG_PARMS}


class ServerError(Exception):
    pass

class Make_netCDFs():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    def process_command_line(self):
        import argparse
        from argparse import RawTextHelpFormatter

        examples = 'Examples:' + '\n\n'
        examples += sys.argv[0] + " -i /mbari/LRAUV/daphne/missionlogs/2015/ -u 'http://elvis.shore.mbari.org/thredds/catalog/LRAUV/daphne/missionlogs/2015/.*.nc4$' -r '10S'"
        parser = argparse.ArgumentParser(formatter_class=RawTextHelpFormatter,
                                         description='Read lRAUV data transferred over hotstpot and .nc file in compatible CF1-6 Discrete Sampling Geometry for for loading into STOQS',
                                         epilog=examples)
        parser.add_argument('-u', '--inUrl',action='store', help='url where processed data logs are. Will be constructed from --platform if not provided.')
        parser.add_argument('-i', '--inDir',action='store', help='url where processed data logs are. Will be constructed from --platform if not provided.')
        parser.add_argument('-a', '--appendString',action='store', help='string to append to the data file created; used to differentiate engineering and science data files',
                            choices=['scieng', 'sci', 'eng'], default='scieng')
        parser.add_argument('-r', '--resampleFreq', action='store', 
                            help='Optional resampling frequency string to specify how to resample interpolated results e.g. 2S=2 seconds, 5Min=5 minutes,H=1 hour,D=daily', default='2S')
        parser.add_argument('-p', '--parms', action='store', help='List of JSON formatted parameter groups, variables and renaming of variables. Will override default for --appendString.')
        parser.add_argument('--start', action='store', help='Start time in YYYYMMDDTHHMMSS format', default='20150930T000000')
        parser.add_argument('--end', action='store', help='Start time in YYYYMMDDTHHMMSS format', default='20151031T000000')
        parser.add_argument('--trackingdb', action='store_true', help='Attempt to use positions of <name>_ac from the Tracking Database (ODSS)')
        parser.add_argument('--nudge', action='store_true', help='Nudge the dead reckoned positions to meet the GPS fixes')
        parser.add_argument('--platform', action='store', help='Platform name: tethys, daphne, ahi, ...')
        parser.add_argument('--previous_month', action='store_true', help='Create files for the previous month')
        parser.add_argument('--current_month', action='store_true', help='Create files for the current month')
        parser.add_argument('-v', '--verbose', nargs='?', choices=[1,2,3], type=int, help='Turn on verbose output. If > 2 load is verbose too.', const=1, default=0)

        self.args = parser.parse_args()

        if self.args.verbose >= 1:
            self.logger.setLevel(logging.DEBUG)
        elif self.args.verbose > 0:
            self.logger.setLevel(logging.INFO)

    def assign_parms(self):
        '''Assign the parms dictionary accordingly. Set to parms associated 
        with appendString, override if --parms specified
        '''
        if self.args.appendString == 'scieng':
            parms = SCIENG_PARMS
        if self.args.appendString == 'sci':
            parms = SCI_PARMS
        if self.args.appendString == 'eng':
            parms = ENG_PARMS

        if self.args.parms:
            # Check formatting of json arguments - this is easy to mess up
            try:
                parms = json.loads(self.args.parms)
            except Exception as e:
                self.logger.warning('Parameter argument invalid {}'.format(self.args.parms))
                exit(-1)

        return parms

    def assign_dates(self):
        # Unless start time defined, then start there
        if self.args.start is not None:
            try:
                start = datetime.strptime(self.args.start, '%Y%m%dT%H%M%S')
            except ValueError:
                start = datetime.strptime(self.args.start, '%Y%m%d')
        else:
            start = None

        if self.args.end is not None:
            try:
                end = datetime.strptime(self.args.end, '%Y%m%dT%H%M%S')
            except ValueError:
                end = datetime.strptime(self.args.end, '%Y%m%d')
        else:
            end = None

        return start, end

    def assign_ins(self, start, end, platform):
        '''Default is to return inDir and inURL given platform and datetime parameters
        '''
        if self.args.inDir:
            inDir = self.args.inDir
        else:
            inDir = f"/mbari/LRAUV/{platform}/missionlogs/{start.year}"

        if self.args.inUrl:
            inUrl = self.args.inUrl
        else:
            inUrl = f"http://elvis.shore.mbari.org/thredds/catalog/LRAUV/{platform}/missionlogs/{start.year}/.*.nc4"

        return inDir, inUrl

    def find_urls(self, base, select, startdate, enddate):
        url = os.path.join(base, 'catalog.xml')
        skips = Crawl.SKIPS + [".*Courier*", ".*Express*", ".*Normal*, '.*Priority*", ".*.cfg$" ]
        u = urlparse(url)
        name, ext = os.path.splitext(u.path)
        if ext == ".html":
            u = urlparse(url.replace(".html", ".xml"))
        url = u.geturl()
        urls = []

        self.logger.debug(f"Crawling {url} looking for .dlist files")
        dlist_cat = Crawl(url, select=[".*dlist"])

        # Crawl the catalogRefs:
        self.logger.info(f"Crawling {url} for {files} files to make {self.args.resampleFreq}_{self.args.appendString}.nc files")
        for dataset in dlist_cat.datasets:
            # get the mission directory name and extract the start and ending dates
            dlist = os.path.basename(dataset.id)
            mission_dir_name = dlist.split('.')[0]
            dts = mission_dir_name.split('_')
            dir_start =  datetime.strptime(dts[0], '%Y%m%d')
            dir_end =  datetime.strptime(dts[1], '%Y%m%d')

            # if within a valid range, grab the valid urls
            self.logger.debug(f"Checking if .dlist {dlist} is within {startdate} and {enddate}")
            if dir_start >= startdate and dir_end <= enddate:
                catalog = '{}_{}/catalog.xml'.format(dir_start.strftime('%Y%m%d'), dir_end.strftime('%Y%m%d'))
                self.logger.debug(f"Crawling {os.path.join(base, catalog)}")
                log_cat = Crawl(os.path.join(base, catalog), select=[select], skip=skips)
                self.logger.debug(f"Getting opendap urls from datasets {log_cat.datasets}")
                d = [s.get("url") for d in log_cat.datasets for s in d.services if s.get("service").lower() == "opendap"]
                for url in d:
                    self.logger.debug(f"Adding url {url}")
                    urls.append(url)

        return urls

    def validate_urls(self, potential_urls, inDir):
        urls = []
        for url in potential_urls:
            try:
                startDatetime, endDatetime = self.getNcStartEnd(inDir, url, 'time_time')
            except Exception as e:
                # Write a message to the .log file for the expected output file so that
                # lrauv-data-file-audit.sh can detect the problem
                log_file = os.path.join(inDir, '/'.join(url.split('/')[9:]))
                log_file = log_file.replace('.nc4', '_' + self.args.resampleFreq + '_' + self.args.appendString + '.log')

                fh = logging.FileHandler(log_file, 'w+')
                frm = logging.Formatter("%(levelname)s %(asctime)sZ %(filename)s %(funcName)s():%(lineno)d %(message)s")
                fh.setFormatter(frm)
                self.logger.addHandler(fh)
                self.logger.warn(f"Can't get start and end date from .nc4: time_time not found in {url}")
                fh.close()
                sh = logging.StreamHandler()
                sh.setFormatter(frm)
                self.logger.handlers = [sh]
                continue

            self.logger.debug('startDatetime, endDatetime = {}, {}'.format(startDatetime, endDatetime))

            if start is not None and startDatetime <= start :
                self.logger.info('startDatetime = {} out of bounds with user-defined startDatetime = {}'.format(startDatetime, start))
                continue

            if end is not None and endDatetime >= end :
                self.logger.info('endDatetime = {} out of bounds with user-defined endDatetime = {}'.format(endDatetime, end))
                continue

            urls.append(url)

        return urls

    def getNcStartEnd(self, inDir, urlNcDap, timeAxisName):
        '''Find the lines in the html with the .nc file, then open it and read the start/end times
        return url to the .nc  and start/end as datetime objects.
        '''
        self.logger.debug('open_url on urlNcDap = {}'.format(urlNcDap))

        try:
            base_in =  '/'.join(urlNcDap.split('/')[-3:])
            in_file = os.path.join(inDir, base_in) 
            df = netCDF4.Dataset(in_file, mode='r')
        except pydap.exceptions.ServerError as ex:
            self.logger.warning(ex)
            raise ServerError("Can't read {} time axis from {}".format(timeAxisName, urlNcDap))

        try:
            timeAxisUnits = df[timeAxisName].units
        except KeyError as ex:
            self.logger.warning(ex)
            raise ServerError("Can't read {} time axis from {}".format(timeAxisName, urlNcDap))

        if timeAxisUnits == 'seconds since 1970-01-01T00:00:00Z' or timeAxisUnits == 'seconds since 1970/01/01 00:00:00Z':
            timeAxisUnits = 'seconds since 1970-01-01 00:00:00'    # coards is picky

        try:
            startDatetime = from_udunits(df[timeAxisName][0].data, timeAxisUnits)
            endDatetime = from_udunits(df[timeAxisName][-1].data, timeAxisUnits)
        except pydap.exceptions.ServerError as ex:
            self.logger.warning(ex)
            raise ServerError("Can't read start and end dates of {} from {}".format(timeAxisUnits, urlNcDap))
        except ValueError as ex:
            self.logger.warning(ex)
            raise ServerError("Can't read start and end dates of {} from {}".format(timeAxisUnits, urlNcDap))

        return startDatetime, endDatetime


    def processResample(self, pw, url_in, inDir, resample_freq, parms, rad_to_deg, appendString):
        '''
        Created resampled LRAUV data netCDF file
        '''
        url_o = None

        self.logger.debug('url = {}'.format(url_in))
        url_out = url_in.replace('.nc4', '_' + resample_freq + '_' + appendString + '.nc')
        base_in =  '/'.join(url_in.split('/')[-3:])
        base_out = '/'.join(url_out.split('/')[-3:])

        out_file = os.path.join(inDir,  base_out)
        in_file =  os.path.join(inDir,  base_in)

        try:
            if not os.path.exists(out_file):
                # The --trackingdb and --nudge args are needed here via self.args
                pw.processResampleNc4File(in_file, out_file, parms, resample_freq, rad_to_deg, self.args)
            else:
                self.logger.info(f"Not calling processResampleNc4File() for {out_file}: file exists")
        except TypeError as te:
            self.logger.warning('Problem reading data from {}'.format(url_in))
            self.logger.warning('Assuming data are invalid and skipping')
            self.logger.warning(te)
            raise te
        except IndexError as ie:
            self.logger.warning('Problem interpolating data from {}'.format(url_in))
            raise ie
        except KeyError:
            raise ServerError("Key error - can't read parameters from {}".format(url_in))
        ##except ValueError as e:
        ##    raise ServerError("ValueError: {} - can't read parameters from {}".format(e, url_in))

        url_o = url_out
        return url_o


if __name__ == '__main__':

    mn = Make_netCDFs()
    mn.process_command_line()
    parms = mn.assign_parms()
    start, end = mn.assign_dates()

    if mn.args.platform:
        platforms = [mn.args.platform]
    else:
        platforms = lrauvs

    for platform in platforms:
        mn.logger.debug(f"Processing new .nc4 data from platform {platform}")
        inDir, inUrl = mn.assign_ins(start, end, platform)
        url, files = inUrl.rsplit('/', 1)
        potential_urls = mn.find_urls(url, files, start, end)
        urls = mn.validate_urls(potential_urls, inDir)


        pw = lrauvNc4ToNetcdf.InterpolatorWriter()

        # Look in time order - oldest to newest
        convert_radians = True
        for url in sorted(urls):
            try:
                mn.processResample(pw, url, inDir, mn.args.resampleFreq, parms, convert_radians, mn.args.appendString, mn.args)
            except ServerError as e:
                mn.logger.warning(e)
                continue

