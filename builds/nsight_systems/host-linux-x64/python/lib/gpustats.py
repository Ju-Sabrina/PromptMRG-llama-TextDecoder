import nsysstats

# Used as intermediate class to create GPU operation tables including the profiling overhead.
class GPUOperation(nsysstats.ExpertSystemsReport):
    query_union = """
            UNION ALL
        """

    def __init__(self, dbfile, args=[]):
        super().__init__(dbfile, args)

        self._gpu_ops_tables = {
            "GPU_CUDA": self._query_cuda_gpu_ops(),
            "GPU_VULKAN": self._query_vulkan_gpu_ops(),
            "GPU_OPENGL": self._query_opengl_gpu_ops(),
            "GPU_DX12": self._query_dx12_gpu_ops()
        }

        self._gpu_ops_tables = {key: value for key, value in self._gpu_ops_tables.items() if value is not None}
        self._query_gpu_ops_union = ""

    def query_gpu_ops_union(self):
        return self._query_gpu_ops_union

    def _select_gpu_ops_columns(self, table: str, api: str, global_id='globalPid', device_id='deviceId', context_id='contextId'):
        query = """
            SELECT
                start,
                end,
                ({GLOBAL_ID} >> 24) & 0x00FFFFFF AS pid,
                {GLOBAL_ID} AS globalId,
                {DEVICE_ID} AS deviceId,
                {CONTEXT_ID} AS contextId,
                '{API}' AS api
            FROM
                {TABLE}
            WHERE
                start > 0
        """

        return query.format(
            TABLE = table,
            GLOBAL_ID = global_id,
            DEVICE_ID = device_id,
            CONTEXT_ID = context_id,
            API = api
        )

    def _add_profiling_overhead(self, gpu_ops_table: str, overhead_condition='false'):
        if self.table_exists('PROFILER_OVERHEAD'):
            # Add the profiling overhead to the GPU operation table
            # 1. CTE "range": Get [min(start), max(end)] for each deviceId/PID. It will be
            #    used as the clipping range for overheads.
            # 2. CTE "overhead": Select the profiling overhead that we want to take into
            #    account.
            # 3. Duplicate overhead rows for each deviceId/PID. This will create a deviceId
            #    column that is not initially in the PROFILER_OVERHEAD table.
            #    Note: a profiling overhead on one thread affects all GPUs of the same
            #    process.
            # 4. The overhead rows are combined with GPU operation rows.
            query_overhead = """
                WITH
                    gpuops AS (
                        {TABLE}
                    ),
                    range AS (
                        SELECT
                            min(start) AS start,
                            max(end) AS end,
                            pid,
                            globalId,
                            deviceId,
                            contextId,
                            api
                        FROM
                            gpuops
                        GROUP BY deviceId, pid
                    ),
                    overheadID AS (
                        SELECT
                            id
                        FROM
                            StringIds
                        WHERE
                            {CONDITION}
                    ),
                    overhead AS (
                        SELECT
                            po.start,
                            po.end,
                            (po.globalTid >> 24) & 0x00FFFFFF AS pid
                        FROM
                            PROFILER_OVERHEAD AS po
                        JOIN
                            overheadID AS co
                            ON co.id == po.nameId
                    )
                SELECT
                    co.start,
                    co.end,
                    co.pid,
                    range.globalId,
                    range.deviceId,
                    range.contextId,
                    range.api
                FROM
                    overhead AS co
                JOIN
                    range
                    ON      co.pid == range.pid
                        AND co.start > range.start
                        AND co.end < range.end
                UNION ALL
                SELECT
                    *
                FROM
                    gpuops
            """

            gpu_ops_table = query_overhead.format(
                TABLE = gpu_ops_table,
                CONDITION = overhead_condition
            )

        return gpu_ops_table

    def _query_cuda_gpu_ops(self):
        sub_queries = []

        kernel = 'CUPTI_ACTIVITY_KIND_KERNEL'
        memcpy = 'CUPTI_ACTIVITY_KIND_MEMCPY'
        memset = 'CUPTI_ACTIVITY_KIND_MEMSET'

        if self.table_exists(kernel):
            sub_queries.append(self._select_gpu_ops_columns(kernel, 'cuda'))

        if self.table_exists(memcpy):
            sub_queries.append(self._select_gpu_ops_columns(memcpy, 'cuda'))

        if self.table_exists(memset):
            sub_queries.append(self._select_gpu_ops_columns(memset, 'cuda'))

        if len(sub_queries) == 0:
            return

        ops = self.query_union.join(sub_queries)
        overhead_condition = "value == 'CUDA profiling data flush overhead' \
                OR value == 'CUDA profiling stop overhead' \
                OR value == 'CUDA profiling overhead'"
        return self._add_profiling_overhead(ops, overhead_condition)

    def _query_vulkan_gpu_ops(self):
        vulkan = 'VULKAN_WORKLOAD'

        if not self.table_exists(vulkan):
            return None

        self.table_col_checks[vulkan] = \
            { 'gpu':
                "{DBFILE} could not be analyzed due to missing 'gpu'."
                " Please re-export the report file with a recent version of Nsight Systems." }

        ops = self._select_gpu_ops_columns(vulkan, 'vulkan', 'globalTid', 'gpu')
        return self._add_profiling_overhead(ops, "value == 'Vulkan profiling overhead'")

    def _query_opengl_gpu_ops(self):
        opengl = 'OPENGL_WORKLOAD'

        if not self.table_exists(opengl):
            return None

        ops = self._select_gpu_ops_columns(opengl, 'opengl', 'globalTid', 'gpu')
        return self._add_profiling_overhead(ops, "value == 'OpenGL profiling overhead'")

    def _query_dx12_gpu_ops(self):
        dx12 = 'DX12_WORKLOAD'

        if not self.table_exists(dx12):
            return None

        self.table_col_checks[dx12] = \
            { 'gpu':
                    "{DBFILE} could not be analyzed due to missing 'gpu'."
                    " Please re-export the report file with a recent version of Nsight Systems."}

        ops = self._select_gpu_ops_columns(dx12, 'dx12', 'globalTid', 'gpu', 'shortContextId')
        return self._add_profiling_overhead(ops, "value == 'DX12 profiling overhead'")

    # Creates the GPU operation view for each API and combines them into one.
    # query_to_apply is a string query that needs to be applied to each GPU operation view
    # before the union. It must contain a placeholder with 'GPU_TABLE' as named index.
    def create_gpu_ops_view(self, query_to_apply=None):
        tables_to_union = []

        create_gpu_view = """
            CREATE TEMP VIEW {TABLE} AS
                {QUERY}
        """

        query_gpu_ops = """
            SELECT *
            FROM {TABLE}
        """

        for table_name, query in self._gpu_ops_tables.items():
            if query_to_apply is not None:
                query = query_to_apply.format(GPU_TABLE = query)

            errmsg = self._execute_statement(
                create_gpu_view.format(
                    TABLE = table_name,
                    QUERY = query
                )
            )
            if errmsg != None:
                return errmsg

            tables_to_union.append(query_gpu_ops.format(TABLE = table_name))

        self._query_gpu_ops_union = self.query_union.join(tables_to_union)

    def setup(self):
        if len(self._gpu_ops_tables) == 0:
            return "{DBFILE} could not be analyzed because it does not contain the required data." \
                " Does the application launch GPU operations?"

        err = super().setup()
        if err != None:
            return err
