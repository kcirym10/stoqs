#!/usr/bin/env python
'''
Master load script intended to be called from cron for
routine loading of all LRUAV data available on a DAP server.

Given a year and month argument in the form YYYYMM this script will:

1. Generated a load script for the stoqs_lrauv_monYYYY campaign
2. Enter a line for it in the mbari_lrauv_campaigns.py file
3. Execute the load
4. Execute the --updateprovenance option
5. Create a pg_dump of the database

This script writes load scripts for month's worth of LRAUV data.
It also enters lines into the lruav_campaigns.py file.

Executing the load is accomplished with another step, e.g.:

   stoqs/loaders/load.py --db stoqs_lrauv_may2019

Mike McCann
MBARI 24 September 2019
'''
import os
import sys
stoqs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
sys.path.insert(0, stoqs_dir)
import logging

from argparse import ArgumentParser, Namespace
from datetime import datetime
from dateutil.relativedelta import relativedelta
from make_load_scripts import LoaderMaker
from loaders.load import Loader

class AutoLoad():

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    def _YYYYMM_to_monyyyy(self, YYYYMM):
        return datetime(int(YYYYMM[:4]), int(YYYYMM[4:]), 1).strftime("%b%Y").lower()

    def _do_the_load(self, monyyyy):
        self.loader.args.db = (f"stoqs_lrauv_{monyyyy}", )
        self.logger.debug(f"--db arg set to {self.loader.args.db}")
        self.loader.checks()
        self.logger.debug(f"Executing self.loader.load()...")
        self.loader.load()
        self.logger.debug(f"Executing self.loader.updateprovenance()...")
        self.loader.updateprovenance()
        self.logger.debug(f"Executing self.loader.pg_dump()...")
        self.loader.pg_dump()

    def execute(self):
        lm = LoaderMaker()
        if self.args.verbose:
            self.logger.setLevel(logging.DEBUG)
            lm.logger.setLevel(logging.DEBUG)

        self.loader = Loader()

        # Start with default arguments for Loader - set args to force reload databases
        self.loader.args = Namespace(background=False, campaigns='campaigns', clobber=False, db=None, 
                                drop_indexes=False, email=None, grant_everyone_select=False, 
                                list=False, noinput=False, pg_dump=False, removetest=False, 
                                slack=False, test=False, updateprovenance=False, verbose=0)
        self.loader.args.noinput = True
        self.loader.args.clobber = True
        self.loader.args.test = self.args.test
        self.loader.args.verbose = self.args.verbose

        if self.args.YYYYMM:
            # Ensure that we have load script and campaign created for the year requested
            items = lm.create_load_scripts(int(self.args.YYYYMM[:4]))
            lm.update_lrauv_campaigns(items)
            self._do_the_load(self._YYYYMM_to_monyyyy(YYYYMM))

        elif self.args.start_YYYYMM and self.args.end_YYYYMM:
            for year in range(int(self.args.start_YYYYMM[:4]), int(self.args.end_YYYYMM[:4]) + 1):
                # Ensure that we have load scripts and campaigns created for the year requested
                items = lm.create_load_scripts(year)
                lm.update_lrauv_campaigns(items)
                if year == int(self.args.start_YYYYMM[:4]):
                    for month in range(int(self.args.start_YYYYMM[4:]), 13):
                        self._do_the_load(self._YYYYMM_to_monyyyy(f"{year}{month:02d}"))
                if year != int(self.args.start_YYYYMM[:4]) and year != int(self.args.end_YYYYMM[:4]):
                    for month in range(1, 13):
                        self._do_the_load(self._YYYYMM_to_monyyyy(f"{year}{month:02d}"))
                if year == int(self.args.end_YYYYMM[:4]):
                    for month in range(1, int(self.args.end_YYYYMM[4:])):
                        self._do_the_load(self._YYYYMM_to_monyyyy(f"{year}{month:02d}"))

        elif self.args.previous_month:
            prev_mon = datetime.today() - relativedelta(months=1)
            # Ensure that we have load script and campaign created for the year requested
            YYYYMM = prev_mon.strftime("%Y%m")
            items = lm.create_load_scripts(int(YYYYMM[4:]))
            lm.update_lrauv_campaigns(items)
            self._do_the_load(self._YYYYMM_to_monyyyy(YYYYMM))

        elif self.args.current_month:
            curr_mon = datetime.today()
            # Ensure that we have load script and campaign created for the year requested
            YYYYMM = curr_mon.strftime("%Y%m")
            items = lm.create_load_scripts(int(YYYYMM[4:]))
            lm.update_lrauv_campaigns(items)
            self._do_the_load(self._YYYYMM_to_monyyyy(YYYYMM))

    def process_command_line(self):
        parser = ArgumentParser()
        parser.add_argument('--YYYYMM', action='store', help='Year and month for database to recreate, e.g. 201906')
        parser.add_argument('--start_YYYYMM', action='store', help='Start year and month, e.g. 201701')
        parser.add_argument('--end_YYYYMM', action='store', help='End year and month, e.g. 201812')
        parser.add_argument('--previous_month', action='store_true', help='Recreate the database for the previous month')
        parser.add_argument('--current_month', action='store_true', help='Recreate the database for the current month')
        parser.add_argument('--test', action='store_true', help='Load test database(s)')
        parser.add_argument('-v', '--verbose', nargs='?', choices=[1,2,3], type=int, 
                            help='Turn on verbose output. If > 2 load is verbose too.', const=1, default=0)

        self.args = parser.parse_args()
        if (self.args.YYYYMM or (self.args.start_YYYYMM and self.args.end_YYYYMM) 
            or self.args.previous_month or self.args.current_month):
            pass
        else: 
            parser.print_help(sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    autoload = AutoLoad()
    autoload.process_command_line()
    autoload.execute()

