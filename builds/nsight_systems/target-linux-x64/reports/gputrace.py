#!/usr/bin/env python

import nsysstats

class CUDAGPUTrace(nsysstats.StatsReport):

    display_name = 'CUDA GPU Trace'
    usage = f"""{{SCRIPT}} -- CUDA GPU Trace

    No arguments.

    Output: All time values default to nanoseconds
        Start : Timestamp of start time
        Duration : Length of event
        CorrId : Correlation ID
        GrdX, GrdY, GrdZ : Grid values
        BlkX, BlkY, BlkZ : Block values
        Reg/Trd : Registers per thread
        StcSMem : Size of Static Shared Memory
        DymSMem : Size of Dynamic Shared Memory
        Bytes : Size of memory operation
        Throughput : Memory throughput
        SrcMemKd : Memcpy source memory kind or memset memory kind
        DstMemKd : Memcpy destination memory kind
        Device : GPU device name and ID
        Ctx : Context ID
        Strm : Stream ID
        Name : Trace event name

    This report displays a trace of CUDA kernels and memory operations.
    Items are sorted by start time.
"""

    query_stub = """
WITH
    {MEM_KIND_STRS_CTE}
    {MEM_OPER_STRS_CTE}
    recs AS (
        {GPU_SUB_QUERY}
    )
    SELECT
        start AS "Start:ts_ns",
        duration AS "Duration:dur_ns",
        correlation AS "CorrId",
        gridX AS "GrdX",
        gridY AS "GrdY",
        gridZ AS "GrdZ",
        blockX AS "BlkX",
        blockY AS "BlkY",
        blockZ AS "BlkZ",
        regsperthread AS "Reg/Trd",
        ssmembytes AS "StcSMem:mem_B",
        dsmembytes AS "DymSMem:mem_B",
        bytes AS "Bytes:mem_B",
        CASE
            WHEN bytes IS NULL
                THEN ''
            ELSE
                bytes * (1000000000 / duration)
        END AS "Throughput:thru_B",
        srcmemkind AS "SrcMemKd",
        dstmemkind AS "DstMemKd",
        device AS "Device",
        context AS "Ctx",
        stream AS "Strm",
        name AS "Name"
    FROM
            recs
    ORDER BY start;
"""

    query_kernel = """
        SELECT
            start AS "start",
            (end - start) AS "duration",
            gridX AS "gridX",
            gridY AS "gridY",
            gridZ AS "gridZ",
            blockX AS "blockX",
            blockY AS "blockY",
            blockZ AS "blockZ",
            registersPerThread AS "regsperthread",
            staticSharedMemory AS "ssmembytes",
            dynamicSharedMemory AS "dsmembytes",
            NULL AS "bytes",
            NULL AS "srcmemkind",
            NULL AS "dstmemkind",
            NULL AS "memsetval",
            printf('%s (%d)', gpu.name, deviceId) AS "device",
            contextId AS "context",
            streamId AS "stream",
            dmn.value AS "name",
            correlationId AS "correlation"
        FROM
            CUPTI_ACTIVITY_KIND_KERNEL
        LEFT JOIN
            StringIds AS dmn
            ON CUPTI_ACTIVITY_KIND_KERNEL.demangledName == dmn.id
        LEFT JOIN
            TARGET_INFO_GPU AS gpu
            ON CUPTI_ACTIVITY_KIND_KERNEL.deviceId == gpu.id
"""

    query_memcpy = """
        SELECT
            start AS "start",
            (end - start) AS "duration",
            NULL AS "gridX",
            NULL AS "gridY",
            NULL AS "gridZ",
            NULL AS "blockX",
            NULL AS "blockY",
            NULL AS "blockZ",
            NULL AS "regsperthread",
            NULL AS "ssmembytes",
            NULL AS "dsmembytes",
            bytes AS "bytes",
            msrck.name AS "srcmemkind",
            mdstk.name AS "dstmemkind",
            NULL AS "memsetval",
            printf('%s (%d)', gpu.name, deviceId) AS "device",
            contextId AS "context",
            streamId AS "stream",
            memopstr.name AS "name",
            correlationId AS "correlation"
        FROM
            CUPTI_ACTIVITY_KIND_MEMCPY AS memcpy
        LEFT JOIN
            MemcpyOperStrs AS memopstr
            ON memcpy.copyKind == memopstr.id
        LEFT JOIN
            MemKindStrs AS msrck
            ON memcpy.srcKind == msrck.id
        LEFT JOIN
            MemKindStrs AS mdstk
            ON memcpy.dstKind == mdstk.id
        LEFT JOIN
            TARGET_INFO_GPU AS gpu
            ON memcpy.deviceId == gpu.id
"""

    query_memset = """
        SELECT
            start AS "start",
            (end - start) AS "duration",
            NULL AS "gridX",
            NULL AS "gridY",
            NULL AS "gridZ",
            NULL AS "blockX",
            NULL AS "blockY",
            NULL AS "blockZ",
            NULL AS "regsperthread",
            NULL AS "ssmembytes",
            NULL AS "dsmembytes",
            bytes AS "bytes",
            mk.name AS "srcmemkind",
            NULL AS "dstmemkind",
            value AS "memsetval",
            printf('%s (%d)', gpu.name, deviceId) AS "device",
            contextId AS "context",
            streamId AS "stream",
            '[CUDA memset]' AS "name",
            correlationId AS "correlation"
        FROM
            CUPTI_ACTIVITY_KIND_MEMSET AS memset
        LEFT JOIN
            MemKindStrs AS mk
            ON memset.memKind == mk.id
        LEFT JOIN
            TARGET_INFO_GPU AS gpu
            ON memset.deviceId == gpu.id
"""

    query_union = """
        UNION ALL
"""

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        sub_queries = []

        if self.table_exists('CUPTI_ACTIVITY_KIND_KERNEL'):
            sub_queries.append(self.query_kernel)

        if self.table_exists('CUPTI_ACTIVITY_KIND_MEMCPY'):
            sub_queries.append(self.query_memcpy)

        if self.table_exists('CUPTI_ACTIVITY_KIND_MEMSET'):
            sub_queries.append(self.query_memset)

        if len(sub_queries) == 0:
            return "{DBFILE} does not contain GPU trace data."

        self.query = self.query_stub.format(
            MEM_OPER_STRS_CTE = self.MEM_OPER_STRS_CTE,
            MEM_KIND_STRS_CTE = self.MEM_KIND_STRS_CTE,
            GPU_SUB_QUERY = self.query_union.join(sub_queries))

if __name__ == "__main__":
    CUDAGPUTrace.Main()
