#!/usr/bin/env python

import nsysstats

class CUDAGPUMemorySizeSummary(nsysstats.StatsReport):

    display_name = 'GPU MemOps Summary (by Size)'
    usage = f"""{{SCRIPT}} -- GPU Memory Operations Summary (by Size)

    No arguments.

    Output:
        Total : Total memory utilized by this operation
        Count : Number of executions of this operation
        Avg : Average memory size of this operation
        Med : Median memory size of this operation
        Min : Smallest memory size of this operation
        Max : Largest memory size of this operation
        StdDev : Standard deviation of the memory size of this operation
        Name : Name of the operation

    This report provides a summary of GPU memory operations and
    the amount of memory they utilize.
"""

    query_stub = """
WITH
    {MEM_OPER_STRS_CTE}
    memops AS (
        {MEM_SUB_QUERY}
    ),
    summary AS (
        SELECT
            name AS name,
            sum(size) AS total,
            count(*) AS num,
            avg(size) AS avg,
            median(size) AS med,
            min(size) AS min,
            max(size) AS max,
            stdev(size) AS stddev
        FROM memops
        GROUP BY 1
    )
SELECT
    summary.total AS "Total:mem_B",
    summary.num AS "Count",
    summary.avg AS "Avg:mem_B",
    summary.med AS "Med:mem_B",
    summary.min AS "Min:mem_B",
    summary.max AS "Max:mem_B",
    summary.stddev AS "StdDev:mem_B",
    summary.name AS "Operation"
FROM
    summary
ORDER BY 1 DESC
;
"""

    query_memcpy = """
        SELECT
            mos.name AS name,
            mcpy.bytes AS size
        FROM
            CUPTI_ACTIVITY_KIND_MEMCPY as mcpy
        INNER JOIN
            MemcpyOperStrs AS mos
            ON mos.id == mcpy.copyKind
"""

    query_memset = """
        SELECT
            '[CUDA memset]' AS name,
            bytes AS size
        FROM
            CUPTI_ACTIVITY_KIND_MEMSET
"""

    query_union = """
        UNION ALL
"""

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        sub_queries = []

        if self.table_exists('CUPTI_ACTIVITY_KIND_MEMCPY'):
            sub_queries.append(self.query_memcpy)

        if self.table_exists('CUPTI_ACTIVITY_KIND_MEMSET'):
            sub_queries.append(self.query_memset)

        if len(sub_queries) == 0:
            return "{DBFILE} does not contain GPU memory data."

        self.query = self.query_stub.format(
            MEM_OPER_STRS_CTE = self.MEM_OPER_STRS_CTE,
            MEM_SUB_QUERY = self.query_union.join(sub_queries))

if __name__ == "__main__":
    CUDAGPUMemorySizeSummary.Main()
