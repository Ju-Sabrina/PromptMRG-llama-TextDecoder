#!/usr/bin/env python

import nsysstats

class CUDAAPIandGPUSummary(nsysstats.StatsReport):

    display_name = 'CUDA Summary (API+Kernels+MemOps)'
    usage = f'''{{SCRIPT}}[:base|:mangled] -- API & GPU Summary (CUDA API + kernels + mem ops)

    base - Optional argument, if given, will cause summary to be over the
        base name of the kernel, rather than the templated name.

    mangled - Optional argument, if given, will cause summary to be over the
        raw mangled name of the kernel, rather than the templated name.

        Note: the ability to display mangled names is a recent addition to the
        report file format, and requires that the profile data be captured with
        a recent version of Nsys. Re-exporting an existing report file is not
        sufficient. If the raw, mangled kernel name data is not available, the
        default demangled names will be used.

    Output: All time values default to nanoseconds
        Time : Percentage of "Total Time"
        Total Time : Total time used by all executions of this kernel
        Instances: Number of executions of this object
        Avg : Average execution time of this kernel
        Med : Median execution time of this kernel
        Min : Smallest execution time of this kernel
        Max : Largest execution time of this kernel
        StdDev : Standard deviation of execution time of this kernel
        Category : Category of the operation
        Operation : Name of the kernel

    This report provides a summary of CUDA API calls, kernels and memory
    operations, and their execution times. Note that the "Time"
    column is calculated using a summation of the "Total Time" column,
    and represents that API call's, kernel's, or memory operation's
    percent of the execution time of the APIs, kernels and memory
    operations listed, and not a percentage of the application wall or
    CPU execution time.

    This report combines data from the "cudaapisum", "gpukernsum", and
    "gpumemsizesum" reports.  It is very similar to profile section of
    "nvprof --dependency-analysis".
'''

    query_stub = """
WITH
    {MEM_OPER_STRS_CTE}
    apigpu AS (
        {SUB_QUERY}
    ),
    summary AS (
        SELECT
            name AS name,
            category AS category,
            sum(duration) AS total,
            count(*) AS num,
            avg(duration) AS avg,
            median(duration) AS med,
            min(duration) AS min,
            max(duration) AS max,
            stdev(duration) AS stddev
        FROM
            apigpu
        GROUP BY 1
    ),
    totals AS (
        SELECT sum(total) AS total
        FROM summary
    )
SELECT
    round(summary.total * 100.0 / (SELECT total FROM totals), 1) AS "Time:ratio_%",
    summary.total AS "Total Time:dur_ns",
    summary.num AS "Instances",
    round(summary.avg, 1) AS "Avg:dur_ns",
    round(summary.med, 1) AS "Med:dur_ns",
    summary.min AS "Min:dur_ns",
    summary.max AS "Max:dur_ns",
    round(summary.stddev, 1) AS "StdDev:dur_ns",
    summary.category AS "Category",
    summary.name AS "Operation"
FROM
    summary
ORDER BY 2 DESC
;
"""

    query_runtime = """
        SELECT
            CASE substr(str.value, -6, 2)
                WHEN '_v' THEN substr(str.value, 1, length(str.value)-6)
                ELSE str.value
            END AS name,
            rt.end - rt.start AS duration,
            'CUDA_API' AS category
        FROM
            CUPTI_ACTIVITY_KIND_RUNTIME AS rt
        LEFT OUTER JOIN
            StringIds AS str
            ON str.id == rt.nameId
"""

    query_kernel = """
        SELECT
            str.value AS name,
            kern.end - kern.start AS duration,
            'CUDA_KERNEL' AS category
        FROM
            CUPTI_ACTIVITY_KIND_KERNEL AS kern
        LEFT OUTER JOIN
            StringIds AS str
            ON str.id == coalesce(kern.{NAME_COL_NAME}, kern.demangledName)
"""

    query_memcpy = """
        SELECT
            mos.name AS name,
            mcpy.end - mcpy.start AS duration,
            'MEMORY_OPER' AS category
        FROM
            CUPTI_ACTIVITY_KIND_MEMCPY as mcpy
        JOIN
            MemcpyOperStrs AS mos
            ON mos.id == mcpy.copyKind
"""

    query_memset = """
        SELECT
            '[CUDA memset]' AS name,
            end - start AS duration,
            'MEMORY_OPER' AS category
        FROM
            CUPTI_ACTIVITY_KIND_MEMSET
"""

    query_union = """
        UNION ALL
"""

    _arg_opts = [
        [['base'],    {'action': 'store_true'}],
        [['mangled'], {'action': 'store_true'}],
    ]

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        name_col_name = 'demangledName'
        if self.parsed_args.base:
            name_col_name = 'shortName'
        elif (self.parsed_args.mangled and
            self.table_col_exists('CUPTI_ACTIVITY_KIND_KERNEL', 'mangledName')):
            name_col_name = 'mangledName'

        sub_queries = []

        if self.table_exists('CUPTI_ACTIVITY_KIND_RUNTIME'):
            sub_queries.append(self.query_runtime)

        if self.table_exists('CUPTI_ACTIVITY_KIND_KERNEL'):
            sub_queries.append(self.query_kernel.format(NAME_COL_NAME = name_col_name))

        if self.table_exists('CUPTI_ACTIVITY_KIND_MEMCPY'):
            sub_queries.append(self.query_memcpy)

        if self.table_exists('CUPTI_ACTIVITY_KIND_MEMSET'):
            sub_queries.append(self.query_memset)

        if len(sub_queries) == 0:
            return '{DBFILE} does not contain CUDA API, GPU kernel, nor memory operations data.'

        self.query = self.query_stub.format(
            MEM_OPER_STRS_CTE = self.MEM_OPER_STRS_CTE,
            SUB_QUERY = self.query_union.join(sub_queries))

if __name__ == "__main__":
    CUDAAPIandGPUSummary.Main()
