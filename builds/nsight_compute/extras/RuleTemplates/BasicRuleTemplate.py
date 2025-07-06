# import our rule system interface
import NvRules

def get_identifier():
    # an internal identifier used to map rules to a section, whitespace is not allowed
    return "TemplateRule1"

def get_name():
    # a descriptive, user-readable name for the rule, e.g. 'Memory Utilization Analysis'
    return "Basic Template Rule"

def get_description():
    # an optional description for the rule, e.g. 'Analyze memory unit utilization for this kernel'
    return "A rule template, demonstration basic NvRules functionality"

def get_section_identifier():
    # an internal identifier used to map rules to a section, whitespace is not allowed
    return "RuleTemplateSection"

# the main function for a rule, the 'handle' parameter is used to retrieve the rule context
def apply(handle):
    # get the rule context, which provides all remaining functions, access to actions, metrics etc.
    ctx = NvRules.get_context(handle)

    # select the first action (CUDA kernel) from the first range (CUDA stream)
    action = ctx.range_by_idx(0).action_by_idx(0)

    # get the frontend object, which interacts with the UI and profiler report
    fe = ctx.frontend()

    # get two metrics from this action
    grid_size = int(action.metric_by_name("launch__grid_size").as_double())
    block_size = int(action.metric_by_name("launch__block_size").as_double())

    # post a message to the frontend
    fe.message("Kernel " + action.name() + " launch config: " + str(grid_size) + "x" + str(block_size))

    # post a warning message to the frontend
    fe.message(NvRules.IFrontend.MsgType_MSG_WARNING, "This is what a warning of the analysis might look like")

    # load a couple of charts and tables which are defined in small file snippets
    fe.load_chart_from_file("RuleTemplate_bar.chart")
    fe.load_chart_from_file("RuleTemplate_table.chart")
