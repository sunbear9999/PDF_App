"""
core/engine/steps/

Built-in workflow step classes.  Each module here contains exactly one
CustomExecutionStep subclass that encapsulates the logic previously hard-coded
as a ``_run_*`` private method inside MasterActionRunner.

Steps are registered into BlueprintNodeTypeRegistry at boot time via
``build_default_blueprint_node_type_registry()`` in workflow_registry.py,
with their ``step_cls`` field set so the runner can instantiate them by
step_type string without any import-time coupling.
"""
