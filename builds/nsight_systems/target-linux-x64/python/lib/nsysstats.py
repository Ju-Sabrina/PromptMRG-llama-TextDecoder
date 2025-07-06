import sys
import inspect
import os.path
import urllib.parse
import csv
import sqlite3
import re
import argparse

class Report:

    class Error(Exception):
        pass

    class Error_MissingDatabaseFile(Error):
        def __init__(self, filename):
            super().__init__(f'Database file {filename} does not exist.')

    class Error_InvalidDatabaseFile(Error):
        def __init__(self, filename):
            super().__init__(f'Database file {filename} could not be opened and appears to be invalid.')

    class Error_InvalidSQL(Error):
        def __init__(self, sql):
            super().__init__(f'Bad SQL statement: {sql}')

    class Error_ArgumentError(Error):
        def __init__(self, msg):
            super().__init__(msg)

    class ArgumentParser(argparse.ArgumentParser):
        def __init__(self, **kwargs):
            self._options = []
            super().__init__(self, **kwargs)

        def exit(self, status=0, message=None):
            raise Report.Error_ArgumentError(message)

        def error(self, message):
            raise Report.Error_ArgumentError(message)

        # Allow optional arguments without dashes.
        def add_optional_arg(self, *args, **kwargs):
            self._options.extend(args)
            dash_args = []
            for arg in args:
                if arg[0] == '+':
                    dash_args.append(arg[1:])
                else:
                    dash_args.append('--' + arg)
            return self.add_argument(*dash_args, **kwargs)

        def parse_optional_dashless_args(self, args):
            formatted_args = []
            for arg in args:
                if arg.split('=')[0] in self._options:
                    arg = '--' + arg
                formatted_args.append(arg)
            return self.parse_args(formatted_args)

    # SQL Aggregate function that takes two arguments: start and end.  Finds the
    # total duration where at least one range is active, but doesn't over-count
    # when events overlap.  Events can be fed in any order.
    class SQLiteAggregateUniqueDuration:
        def __init__(self):
            self.segments = []

        def step(self, start, end):
            if start >= end:
                return
            new_segs = []
            new_start = start
            new_end = end

            for s in self.segments:
                if start <= s[1] and end >= s[0]:
                    new_start = min(new_start, s[0])
                    new_end = max(new_end, s[1])
                else:
                    new_segs.append(s)

            new_segs.append([new_start, new_end])
            self.segments = new_segs

        def finalize(self):
            dur = 0
            for s in self.segments:
                dur += s[1] - s[0]
            self.segments = []
            return dur

    EXIT_HELP = 25
    EXIT_DB = 26
    EXIT_NODATA = 27
    EXIT_SCRIPT = 28
    EXIT_INVALID_ARG = 29

    DEFAULT_ROW_LIMIT = 50

    MEM_KIND_STRS_CTE = """
    MemKindStrs (id, name) AS (
    VALUES
        (0,     'Pageable'),
        (1,     'Pinned'),
        (2,     'Device'),
        (3,     'Array'),
        (4,     'Managed'),
        (5,     'Device Static'),
        (6,     'Managed Static'),
        (7,     'Unknown')
    ),
"""

    MEM_OPER_STRS_CTE = """
    MemcpyOperStrs (id, name) AS (
    VALUES
        (0,     '[CUDA memcpy Unknown]'),
        (1,     '[CUDA memcpy HtoD]'),
        (2,     '[CUDA memcpy DtoH]'),
        (3,     '[CUDA memcpy HtoA]'),
        (4,     '[CUDA memcpy AtoH]'),
        (5,     '[CUDA memcpy AtoA]'),
        (6,     '[CUDA memcpy AtoD]'),
        (7,     '[CUDA memcpy DtoA]'),
        (8,     '[CUDA memcpy DtoD]'),
        (9,     '[CUDA memcpy HtoH]'),
        (10,    '[CUDA memcpy PtoP]'),
        (11,    '[CUDA Unified Memory memcpy HtoD]'),
        (12,    '[CUDA Unified Memory memcpy DtoH]'),
        (13,    '[CUDA Unified Memory memcpy DtoD]')
    ),
"""

    _LOAD_TABLE_QUERY = """
        SELECT name
        FROM sqlite_master
        WHERE type LIKE 'table'
           OR type LIKE 'view';
"""

    _CREATE_FILTERED_VIEW_QUERY = """
        CREATE TEMP VIEW {TABLE} AS
            SELECT rowid, *
            FROM main.{TABLE}
            WHERE ((start >= {START} AND start < {END})
                OR (end >= {START} AND end < {END})
                OR (start < {START} AND end >= {END}))
    """

    _FIND_NVTX_RANGE_QUERY = """
        WITH
            domain AS (
                SELECT
                    domainId,
                    globalTid,
                    text
                FROM
                    NVTX_EVENTS
                WHERE
                    eventType == 75 -- EVENT_TYPE_NVTX_DOMAIN_CREATE
            )
        SELECT
            nvtx.start,
            nvtx.end,
            nvtx.globalTid
        FROM
            NVTX_EVENTS AS nvtx
        LEFT JOIN
            domain
            ON      nvtx.domainId == domain.domainId
                AND nvtx.globalTid >> 24 == domain.globalTid >> 24
        LEFT JOIN
            StringIds AS sid
            ON nvtx.textId == sid.id
        WHERE
                nvtx.eventType IN (59, 60, 70, 71) -- EVENT_TYPE_NVTX[T]_(PUSHPOP|STARTEND)_RANGE
            AND coalesce(nvtx.text, sid.value) || coalesce('@' || domain.text, '') == '{NVTX_RANGE}'
        ORDER BY 1
        LIMIT 1
    """

    _boilerplate_statements = [
        f'pragma cache_size=-{32 * 1024}',      # Set main DB page cache to 32MB
        f'pragma temp.cache_size=-{32 * 1024}', # Set temp DB page cache to 32MB
    ]

    script_name = None
    display_name = 'NO NAME GIVEN'
    usage = "{SCRIPT} -- NO USAGE INFORMATION PROVIDED"
    should_display = True
    table_checks = {}
    table_col_checks = {}
    statements = []
    query = "SELECT 1 AS 'ONE'"

    def __init__(self, dbfile, args=[]):
        self._tables = None
        self._dbcon = None
        self._dbcur = None
        self._dbfile = dbfile
        self._args = args
        self._headers = []

        # Check DB file
        if not os.path.exists(self._dbfile):
            raise self.Error_MissingDatabaseFile(self._dbfile)

        # Open DB file
        dburi_query = {
            'mode': 'ro',
            'nolock': '1',
            'immutable': '1'
        }

        qstr = urllib.parse.urlencode(dburi_query)
        urlstr = urllib.parse.urlunsplit(['file', '', os.path.abspath(self._dbfile), qstr, ''])
        try:
            self._dbcon = sqlite3.connect(urlstr, isolation_level=None, uri=True)
        except sqlite3.Error:
            self._dbcon = None
            raise self.Error_InvalidDatabaseFile(self._dbfile)

        # attach helper functions
        self._dbcon.create_aggregate('unique_duration', 2, self.SQLiteAggregateUniqueDuration)

        # load tables
        try:
            cur = self._dbcon.execute(self._LOAD_TABLE_QUERY)
        except sqlite3.Error:
            raise self.Error_InvalidDatabaseFile(self._dbfile)

        self._tables = set(r[0] for r in cur.fetchall())

    def __del__(self):
        if self._dbcon != None:
            self._dbcon.close()

    def table_exists(self, table):
        return table in self._tables

    def search_tables(self, regex_str):
        regex = re.compile(regex_str)
        matches = []
        for t in self._tables:
            if regex.search(t) != None:
                matches.append(t)
        return matches

    def table_col_exists(self, table, col):
        q = 'SELECT name FROM pragma_table_info(?) WHERE name = ?'
        try:
            cur = self._dbcon.execute(q, (table, col))
        except sqlite3.Error:
            raise self.Error_InvalidSQL(q)

        return cur.fetchone() != None

    def setup(self):
        for table, errmsg in self.table_checks.items():
            if not self.table_exists(table):
                return errmsg
        for table, columns in self.table_col_checks.items():
            for col, errmsg in columns.items():
                if not self.table_col_exists(table, col):
                    return errmsg

        parser = self.ArgumentParser(allow_abbrev=False)
        for opt in self._get_arg_options():
            parser.add_optional_arg(*opt[0], **opt[1])
        self.parsed_args = parser.parse_optional_dashless_args(self.args)

    def get_statements(self):
        return self.statements

    def _execute_statement(self, stmt):
        if self._dbcon == None:
            raise RuntimeError(f'Called {__name__}() with invalid database connection.')

        try:
            self._dbcon.execute(stmt)
        except sqlite3.Error as err:
            return str(err)

    def run_statements(self):
        for stmt in self._boilerplate_statements:
            errmsg = self._execute_statement(stmt)
            if errmsg != None:
                return errmsg

        for stmt in self.get_statements():
            errmsg = self._execute_statement(stmt)
            if errmsg != None:
                return errmsg

    def get_query(self):
        return self.query

    def run_query(self):
        csvw = csv.writer(sys.stdout)
        qcur = self._dbcon.execute(self.get_query())
        qcur.arraysize = 100
        header = list(d[0] for d in qcur.description)
        csvw.writerow(header)

        rows = qcur.fetchmany()
        while rows != []:
            csvw.writerows(rows)
            rows = qcur.fetchmany()

    def start_query(self):
        if self._dbcon == None:
            raise RuntimeError(f'Called {__name__}() with invalid database connection.')
        if self._dbcur != None:
            raise RuntimeError(f'Called {__name__}() more than once.')

        try:
            self._dbcur = self._dbcon.execute(self.get_query())
        except sqlite3.Error as err:
            return str(err)
        self._headers = list(d[0] for d in self._dbcur.description)

    def get_query_row(self):
        if self._dbcon == None:
            raise RuntimeError(f'Called {__name__}() with invalid database connection.')
        if self._dbcur == None:
            raise RuntimeError(f'Called {__name__}() without valid query.')

        row = self._dbcur.fetchone()
        if row == None:
            del self._dbcur
            self._dbcur = None
        return row

    def _query_nvtx_filter_range(self, nvtx):
        if self._dbcon == None:
            raise RuntimeError(f'Called {__name__}() with invalid database connection.')

        if self.table_exists('NVTX_EVENTS'):
            try:
                cur = self._dbcon.execute(self._FIND_NVTX_RANGE_QUERY.format(
                    NVTX_RANGE = nvtx))
            except sqlite3.Error as err:
                return (str(err), None, None, None)

            row = cur.fetchone()
            if row:
                return (None, row[0], row[1], row[2])

        return ("NVTX range '{NVTX_RANGE}' could not be found.".format(NVTX_RANGE = nvtx), None, None, None)

    # Filters tables according to start, end, and nvtx flags, if applicable.
    # Tables that should NOT be filtered (e.g. those used for correlation ID matching)
    # should be prefixed with 'main' in the query.
    def filter_time_range(self, start, end, nvtx):
        if start == None and end == None and nvtx == None:
            return None

        if nvtx != None:
            err, nvtx_start, nvtx_end, globaltid = self._query_nvtx_filter_range(nvtx)
            if err != None:
                return err

            start = nvtx_start if start == None else start
            end = nvtx_end if end == None else end
            pid = (globaltid >> 24) & 0x00FFFFFF
            tid = globaltid & 0x00FFFFFF
        else:
            start = 0 if start == None else start
            end = 0x7FFFFFFFFFFFFFFF if end == None else end
            globaltid = None

        if start > end:
            return "The start time cannot be greater than the end time."

        for table in self._tables:
            if not self.table_col_exists(table, 'start') or not self.table_col_exists(table, 'end'):
                continue

            statement = self._CREATE_FILTERED_VIEW_QUERY.format(
                    TABLE = table, START = start, END = end)

            if globaltid is not None:
                if self.table_col_exists(table, 'globalTid'):
                    if pid != tid:
                        # NVTX Push/Pop range.
                        statement += ' AND globalTid == {GLOBAL_TID}'.format(GLOBAL_TID = globaltid)
                    else:
                        # NVTX Start/End range.
                        statement += ' AND globalTid >> 24 == {GLOBAL_TID} >> 24'.format(GLOBAL_TID = globaltid)
                elif self.table_col_exists(table, 'globalPid'):
                    statement += ' AND globalPid >> 24 == {GLOBAL_TID} >> 24'.format(GLOBAL_TID = globaltid)
                else:
                    continue

            errmsg = self._execute_statement('DROP VIEW IF EXISTS temp.{TABLE}'.format(TABLE = table))
            if errmsg != None:
                return errmsg

            errmsg = self._execute_statement(statement)
            if errmsg != None:
                return errmsg

    @property
    def dbfile(self):
        return self._dbfile

    @property
    def args(self):
        return self._args

    @property
    def headers(self):
        return self._headers

    @classmethod
    def get_script_name(klass):
        if klass.script_name == None:
            klass.script_name = os.path.basename(inspect.getmodule(klass).__file__)
            if klass.script_name.endswith('.py'):
                klass.script_name = klass.script_name[0:-3]
        return klass.script_name

    @classmethod
    def get_display_name(klass):
        return klass.display_name

    @classmethod
    def get_usage_summary(klass):
        return klass.get_usage().split("\n", 1)[0]

    @classmethod
    def get_usage(klass):
        return klass.usage.format(
            SCRIPT=klass.get_script_name(),
            ROW_LIMIT=klass.DEFAULT_ROW_LIMIT)

    @classmethod
    def get_should_display(klass):
        if klass.get_script_name()[0] == '_':
            return False
        return klass.should_display

    @classmethod
    def _get_arg_options(klass):
        opts = []
        for k in klass.__mro__:
            if '_arg_opts' in k.__dict__:
                opts.extend(k._arg_opts)
        return opts

    @classmethod
    def Report(klass, dbfile, args):
        try:
            report = klass(dbfile, args)
        except (klass.Error_MissingDatabaseFile, klass.Error_InvalidDatabaseFile) as err:
            return None, klass.EXIT_DB, str(err)

        # If/when we upgrade to Python 3.9 or higher, look into passing
        # exit_on_error=False to the ArgumentParser constructor and updating
        # how errors are handled with the .exit() function.
        try:
            errmsg = report.setup()
        except klass.Error_ArgumentError as ex:
            return None, klass.EXIT_INVALID_ARG, str(ex)

        if errmsg != None:
            return None, klass.EXIT_NODATA, errmsg.format(DBFILE=report.dbfile)

        errmsg = report.run_statements()
        if errmsg != None:
            return None, klass.EXIT_SCRIPT, errmsg

        errmsg = report.start_query()
        if errmsg != None:
            return None, klass.EXIT_SCRIPT, errmsg

        return report, None, None

    @classmethod
    def Main(klass):
        if len(sys.argv) <= 1:
            print(klass.get_usage())
            exit(klass.EXIT_HELP)

        dbfile = sys.argv[1]
        args = sys.argv[2:]

        report, exitval, errmsg = klass.Report(dbfile, args)
        if report == None:
            print(errmsg, file=sys.stderr)
            exit(exitval)

        csvw = csv.writer(sys.stdout)

        first_row = True
        while True:
            row = report.get_query_row()
            if row == None:
                break
            if first_row:
                first_row = False
                csvw.writerow(report.headers)
            csvw.writerow(row)

class StatsReport(Report):

    def MessageNoResult(self):
        return "Report was successfully run, but no data was returned."

class ExpertSystemsReport(Report):

    DEFAULT_ROW_LIMIT = 50

    _arg_opts = [
        [['rows'],  {'type': int, 'help': 'max rows', 'default': DEFAULT_ROW_LIMIT}],
        [['start'], {'type': int, 'help': 'start time used for filtering'}],
        [['end'],   {'type': int, 'help': 'end time used for filtering'}],
        [['nvtx'],  {'type': str, 'help': 'NVTX range and domain for filtering'}],
    ]

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        self._row_limit = self.parsed_args.rows

        err = self.filter_time_range(self.parsed_args.start,
            self.parsed_args.end, self.parsed_args.nvtx)
        if err != None:
            return err

    message_advice = "NO ADVICE MESSAGE DEFINED"
    message_noresult = "NO NON-RESULT MESSAGE DEFINED"

    def MessageAdvice(self, extended=True):
        if extended and hasattr(self, 'message_advice_extended'):
            return self.message_advice_extended
        return self.message_advice

    def MessageNoResult(self):
        return self.message_noresult

    def MessageRowLimit(self, rows):
        if self._row_limit == 0 or rows < self._row_limit:
            return ''
        if self._row_limit == 1:
            return 'Only the top result is displayed. More data may be available.'
        return f"Only the top {rows} results are displayed. More data may be available."
