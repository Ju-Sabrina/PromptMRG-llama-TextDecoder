#!/usr/bin/env python

# THIS SCRIPT FOR DEBUGGING AND TESTING ONLY
# THIS SCRIPT FOR DEBUGGING AND TESTING ONLY
# THIS SCRIPT FOR DEBUGGING AND TESTING ONLY
# THIS SCRIPT FOR DEBUGGING AND TESTING ONLY

import nsysstats

class TESTReportSQLStatement(nsysstats.StatsReport):

    DEFAULT_QUERY='SELECT 1'

    display_name = 'DEBUG: SQL Statement'
    usage = f"""{{SCRIPT}}[:sql=<sql_statement>] -- Run SQL Statement

    sql : Arbitrary SQLite statement

    Output defined by <sql_statement>.

    This report accepts and executes an arbitrary SQL statement.
    It is mostly for debugging/testing.  If no <sql_statement> is
    given, the statement "{DEFAULT_QUERY}" is executed.
"""

    _arg_opts = [
        [['sql'], {'type': str, 'help': 'SQL stmt', 'default': DEFAULT_QUERY}],
    ]

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        self.query = self.parsed_args.sql

if __name__ == "__main__":
    TESTReportSQLStatement.Main()
