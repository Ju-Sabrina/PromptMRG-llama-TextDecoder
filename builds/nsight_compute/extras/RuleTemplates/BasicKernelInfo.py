import NvRules

def get_identifier():
    return "KernelInfo"

def get_name():
    return "Kernel Information"

def get_description():
    return "Basic kernel information. This independent rule does not map to any section."

# The Evaluate function is used to specify to the rule system this rule's dependencies,
# e.g. the metrics that must have been collected in order for this rule to work properly.
# For rules that are tied to sections, this is guaranteed by the section itself
def evaluate(handle):
    # specify which metrics are required
    NvRules.require_metrics(handle, ["launch__grid_size", "launch__block_size"])

def apply(handle):
    ctx = NvRules.get_context(handle)

    # select the default action (kernel)
    action = ctx.range_by_idx(0).action_by_idx(0)

    # it is now safe to retrieve those metrics, as we declared the dependency in Evaluate
    grid_size = int(action.metric_by_name("launch__grid_size").as_double())
    block_size = int(action.metric_by_name("launch__block_size").as_double())

    # show a message in the user interface
    ctx.frontend().message("Kernel " + action.name() + " launch config: " + str(grid_size) + "x" + str(block_size))
