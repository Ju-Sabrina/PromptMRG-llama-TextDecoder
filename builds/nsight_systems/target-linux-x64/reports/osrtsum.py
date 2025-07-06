#!/usr/bin/env python

import nsysstats

class OSRTSummaryReport(nsysstats.StatsReport):

    display_name = 'OS Runtime Summary'
    usage = f"""{{SCRIPT}} -- OS Runtime Summary

    No arguments.

    Output: All time values default to nanoseconds
        Time : Percentage of "Total Time"
        Total Time : Total time used by all executions of this function
        Num Calls: Number of calls to this function
        Avg : Average execution time of this function
        Med : Median execution time of this function
        Min : Smallest execution time of this function
        Max : Largest execution time of this function
        StdDev : Standard deviation of execution time of this functions
        Name : Name of the function

    This report provides a summary of operating system functions and
    their execution times. Note that the "Time" column is calculated
    using a summation of the "Total Time" column, and represents that
    function's percent of the execution time of the functions listed,
    and not a percentage of the application wall or CPU execution time.
"""

    query = """
WITH
    summary AS (
        SELECT
            nameId AS nameId,
            sum(end - start) AS total,
            count(*) AS num,
            avg(end - start) AS avg,
            median(end - start) AS med,
            min(end - start) AS min,
            max(end - start) AS max,
            stdev(end - start) AS stddev
        FROM
            OSRT_API
        WHERE
            eventClass == 27
        GROUP BY 1
    ),
    totals AS (
        SELECT
            sum(total) AS total
        FROM summary
    )
SELECT
    round(summary.total * 100.0 / (SELECT total FROM totals), 1) AS "Time:ratio_%",
    summary.total AS "Total Time:dur_ns",
    summary.num AS "Num Calls",
    round(summary.avg, 1) AS "Avg:dur_ns",
    round(summary.med, 1) AS "Med:dur_ns",
    summary.min AS "Min:dur_ns",
    summary.max AS "Max:dur_ns",
    round(summary.stddev, 1) AS "StdDev:dur_ns",
    ids.value AS "Name"
FROM
    summary
LEFT JOIN
    StringIds AS ids
    ON ids.id == summary.nameId
ORDER BY 2 DESC
;
"""

    table_checks = {
        'OSRT_API':
            '{DBFILE} does not contain OS Runtime trace data.'
    }

if __name__ == "__main__":
    OSRTSummaryReport.Main()
