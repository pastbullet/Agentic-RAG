"""Tests for FSM IR v1 lowering: transition grouping, typed guards/actions, diagnostics."""

from __future__ import annotations

import re

from src.extract.codegen import generate_code
from src.extract.fsm_ir import (
    _try_parse_action,
    _try_parse_guard,
    lower_all_state_machines,
    lower_state_machine_to_fsm_ir,
)
from src.models import (
    ContextFieldIR,
    NormalizationStatus,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
    StateContextIR,
)


def _make_sm(
    name: str = "TestSM",
    states: list[ProtocolState] | None = None,
    transitions: list[ProtocolTransition] | None = None,
) -> ProtocolStateMachine:
    return ProtocolStateMachine(
        name=name,
        states=states or [
            ProtocolState(name="IDLE", is_initial=True),
            ProtocolState(name="ACTIVE"),
            ProtocolState(name="DONE", is_final=True),
        ],
        transitions=transitions or [],
        source_pages=[1, 2],
    )


# ── Transition grouping ──────────────────────────────────────


class TestTransitionGrouping:
    def test_single_transition_per_block(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(from_state="IDLE", to_state="ACTIVE", event="START"),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        assert len(ir.blocks) == 1
        assert ir.blocks[0].from_state == "IDLE"
        assert ir.blocks[0].event == "START"
        assert len(ir.blocks[0].branches) == 1

    def test_same_state_event_merged_into_one_block(self):
        """Multiple transitions with same (from_state, event) → one block, multiple branches."""
        sm = _make_sm(transitions=[
            ProtocolTransition(from_state="IDLE", to_state="ACTIVE", event="START", condition="flag is set"),
            ProtocolTransition(from_state="IDLE", to_state="DONE", event="START", condition="timeout"),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        assert len(ir.blocks) == 1
        block = ir.blocks[0]
        assert block.from_state == "IDLE"
        assert block.event == "START"
        assert len(block.branches) == 2

    def test_different_events_separate_blocks(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(from_state="IDLE", to_state="ACTIVE", event="START"),
            ProtocolTransition(from_state="IDLE", to_state="DONE", event="STOP"),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        assert len(ir.blocks) == 2

    def test_events_collected(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(from_state="IDLE", to_state="ACTIVE", event="EVT_A"),
            ProtocolTransition(from_state="ACTIVE", to_state="DONE", event="EVT_B"),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        assert "EVT_A" in ir.events
        assert "EVT_B" in ir.events

    def test_states_preserved(self):
        sm = _make_sm()
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        assert len(ir.states) == 3
        assert ir.states[0].name == "IDLE"

    def test_empty_transitions(self):
        sm = _make_sm(transitions=[])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        assert len(ir.blocks) == 0
        assert ir.normalization_status == NormalizationStatus.BLOCKED


# ── Guard parsing ──────────────────────────────────────


class TestGuardParsing:
    def test_timer_fired(self):
        guard = _try_parse_guard("retransmit_timer expires")
        assert guard is not None
        assert guard.kind == "timer_fired"
        assert guard.ref_source == "timer"
        assert guard.field_ref == "retransmit_timer"

    def test_flag_check(self):
        guard = _try_parse_guard("auth flag is set")
        assert guard is not None
        assert guard.kind == "flag_check"
        assert guard.ref_source == "ctx"
        assert guard.field_ref == "auth"

    def test_field_comparison_eq(self):
        guard = _try_parse_guard("state == ESTABLISHED")
        assert guard is not None
        assert guard.kind == "context_field_eq"
        assert guard.ref_source == "ctx"
        assert guard.field_ref == "state"
        assert guard.operator == "=="
        assert guard.value == "ESTABLISHED"

    def test_field_comparison_ne(self):
        guard = _try_parse_guard("status != READY")
        assert guard is not None
        assert guard.kind == "context_field_ne"

    def test_unparseable_returns_none(self):
        guard = _try_parse_guard("some complex natural language condition about things")
        assert guard is None

    def test_empty_returns_none(self):
        guard = _try_parse_guard("")
        assert guard is None


# ── Action parsing ──────────────────────────────────────


class TestActionParsing:
    def test_set_state(self):
        action = _try_parse_action("set state to ESTABLISHED")
        assert action is not None
        assert action.kind == "set_state"
        assert action.ref_source == "ctx"
        assert action.target == "ESTABLISHED"

    def test_emit_message(self):
        action = _try_parse_action("send ACK message")
        assert action is not None
        assert action.kind == "emit_message"
        assert action.ref_source == "msg"

    def test_start_timer(self):
        action = _try_parse_action("start retransmit timer")
        assert action is not None
        assert action.kind == "start_timer"
        assert action.ref_source == "timer"

    def test_cancel_timer(self):
        action = _try_parse_action("cancel keepalive timer")
        assert action is not None
        assert action.kind == "cancel_timer"
        assert action.ref_source == "timer"

    def test_update_field(self):
        action = _try_parse_action("set counter to 0")
        assert action is not None
        assert action.kind == "update_field"
        assert action.ref_source == "ctx"
        assert action.target == "counter"
        assert action.value == "0"

    def test_unparseable_returns_none(self):
        action = _try_parse_action("perform some complex operation on the data structure")
        assert action is None


# ── Branch readiness ──────────────────────────────────────


class TestBranchReadiness:
    def test_next_state_gives_degraded_ready(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(from_state="IDLE", to_state="ACTIVE", event="START"),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        branch = ir.blocks[0].branches[0]
        assert branch.readiness == NormalizationStatus.DEGRADED_READY

    def test_typed_guard_and_actions_gives_ready(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE", to_state="ACTIVE", event="START",
                condition="state == IDLE",
                actions=["set state to ACTIVE"],
            ),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        branch = ir.blocks[0].branches[0]
        assert branch.readiness == NormalizationStatus.READY

    def test_no_next_state_gives_blocked(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(from_state="IDLE", to_state="", event="START"),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        branch = ir.blocks[0].branches[0]
        assert branch.readiness == NormalizationStatus.BLOCKED


# ── Diagnostics ──────────────────────────────────────


class TestDiagnostics:
    def test_unparsed_guard_generates_diagnostic(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE", to_state="ACTIVE", event="START",
                condition="some complex condition",
            ),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        codes = [d.code for d in ir.diagnostics]
        assert "FSM_GUARD_UNPARSED" in codes

    def test_unparsed_action_generates_diagnostic(self):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE", to_state="ACTIVE", event="START",
                actions=["do something complex"],
            ),
        ])
        ir = lower_state_machine_to_fsm_ir(sm, "test")
        codes = [d.code for d in ir.diagnostics]
        assert "FSM_ACTION_UNPARSED" in codes


# ── lower_all_state_machines ──────────────────────────────


class TestLowerAll:
    def test_lowers_all(self):
        schema = ProtocolSchema(
            protocol_name="test",
            state_machines=[
                _make_sm(name="SM1", transitions=[
                    ProtocolTransition(from_state="IDLE", to_state="ACTIVE", event="GO"),
                ]),
                _make_sm(name="SM2", transitions=[
                    ProtocolTransition(from_state="IDLE", to_state="DONE", event="STOP"),
                ]),
            ],
        )
        irs = lower_all_state_machines(schema)
        assert len(irs) == 2
        assert irs[0].name == "SM1"
        assert irs[1].name == "SM2"


# ── Codegen integration: no duplicate case ──────────────────


class TestCodegenNoDuplicateCase:
    def test_no_duplicate_case_in_generated_code(self, tmp_path):
        """The core bug fix: same (state, event) must NOT produce duplicate case."""
        sm = _make_sm(
            name="BFD Session",
            states=[
                ProtocolState(name="Down", is_initial=True),
                ProtocolState(name="Init"),
                ProtocolState(name="Up"),
            ],
            transitions=[
                ProtocolTransition(from_state="Down", to_state="Init", event="RecvDown", condition="local diag == 0"),
                ProtocolTransition(from_state="Down", to_state="Down", event="RecvDown", condition="local diag != 0"),
                ProtocolTransition(from_state="Down", to_state="Init", event="RecvInit"),
                ProtocolTransition(from_state="Init", to_state="Up", event="RecvInit"),
                ProtocolTransition(from_state="Init", to_state="Up", event="RecvUp"),
                ProtocolTransition(from_state="Up", to_state="Down", event="RecvDown"),
            ],
        )
        schema = ProtocolSchema(
            protocol_name="bfd-test",
            source_document="test.pdf",
            state_machines=[sm],
        )
        result = generate_code(schema, str(tmp_path))
        c_files = [f for f in result.files if f.endswith(".c")]
        assert len(c_files) >= 1

        for c_file_path in c_files:
            with open(c_file_path) as fh:
                content = fh.read()

            # Extract all case labels
            cases = re.findall(r"^\s*case\s+(\S+):", content, re.MULTILINE)
            # Within each switch block, there should be no duplicate case values
            # Simple check: look for consecutive duplicate case labels in outer switch
            seen_in_switch: dict[str, set[str]] = {}
            current_outer = None
            for line in content.splitlines():
                line_stripped = line.strip()
                if line_stripped.startswith("case ") and line_stripped.endswith(":"):
                    label = line_stripped[5:-1].strip()
                    indent = len(line) - len(line.lstrip())
                    if indent <= 8:  # outer switch (state)
                        current_outer = label
                        seen_in_switch[current_outer] = set()
                    elif current_outer is not None:  # inner switch (event)
                        inner_set = seen_in_switch.get(current_outer, set())
                        assert label not in inner_set, (
                            f"Duplicate case '{label}' in state '{current_outer}'"
                        )
                        inner_set.add(label)
                        seen_in_switch[current_outer] = inner_set


class TestCodegenGuardDegradation:
    def test_single_guarded_transition_is_not_emitted_as_unconditional_jump(self, tmp_path):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE",
                to_state="ACTIVE",
                event="START",
                condition="counter == 0",
            ),
        ])
        schema = ProtocolSchema(
            protocol_name="demo",
            source_document="test.pdf",
            state_machines=[sm],
        )

        result = generate_code(schema, str(tmp_path))
        content = ""
        for c_file_path in result.files:
            if c_file_path.endswith("_sm_testsm.c"):
                with open(c_file_path) as fh:
                    content = fh.read()
                break

        assert "if (ctx->counter == 0)" in content
        assert "return demo_testsm_STATE_ACTIVE;" in content
        assert re.search(
            r"case demo_testsm_EVENT_START:\s*/\* guard: counter == 0 \*/\s*if \(ctx->counter == 0\)",
            content,
            re.S,
        )

    def test_mixed_raw_and_typed_guards_do_not_generate_invalid_else_if(self, tmp_path):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE",
                to_state="ACTIVE",
                event="START",
                condition="some complex condition",
            ),
            ProtocolTransition(
                from_state="IDLE",
                to_state="DONE",
                event="START",
                condition="counter == 0",
            ),
        ])
        schema = ProtocolSchema(
            protocol_name="demo",
            source_document="test.pdf",
            state_machines=[sm],
        )

        result = generate_code(schema, str(tmp_path))
        content = ""
        for c_file_path in result.files:
            if c_file_path.endswith("_sm_testsm.c"):
                with open(c_file_path) as fh:
                    content = fh.read()
                break

        assert "return demo_testsm_STATE_ACTIVE;\n            else if" not in content
        assert "else if (ctx->counter == 0)" in content

    def test_missing_next_state_stays_in_current_state_instead_of_unknown_symbol(self, tmp_path):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE",
                to_state="",
                event="START",
            ),
        ])
        schema = ProtocolSchema(
            protocol_name="demo",
            source_document="test.pdf",
            state_machines=[sm],
        )

        result = generate_code(schema, str(tmp_path))
        content = ""
        for c_file_path in result.files:
            if c_file_path.endswith("_sm_testsm.c"):
                with open(c_file_path) as fh:
                    content = fh.read()
                break

        assert "STATE_UNSPECIFIED" not in content
        assert "return demo_testsm_STATE_IDLE;" in content


class TestCtxAwareCodegen:
    def test_transition_signature_includes_protocol_context(self, tmp_path):
        sm = _make_sm(transitions=[
            ProtocolTransition(from_state="IDLE", to_state="ACTIVE", event="START"),
        ])
        schema = ProtocolSchema(
            protocol_name="demo",
            source_document="test.pdf",
            state_machines=[sm],
        )

        result = generate_code(schema, str(tmp_path))
        header_text = ""
        for path in result.files:
            if path.endswith("_sm_testsm.h"):
                header_text = open(path, encoding="utf-8").read()
                break

        assert '#include "demo_context.h"' in header_text
        assert "demo_testsm_transition(demo_testsm_state current_state, demo_testsm_event event, demo_context *ctx);" in header_text

    def test_set_state_generates_context_state_assignment(self, tmp_path):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE",
                to_state="ACTIVE",
                event="START",
                actions=["set state to ACTIVE"],
            ),
        ])
        schema = ProtocolSchema(
            protocol_name="demo",
            source_document="test.pdf",
            state_machines=[sm],
        )

        result = generate_code(schema, str(tmp_path))
        source_text = ""
        for path in result.files:
            if path.endswith("_sm_testsm.c"):
                source_text = open(path, encoding="utf-8").read()
                break

        assert "ctx->session_state = DEMO_CTX_STATE_ACTIVE;" in source_text
        assert result.generated_action_count == 1

    def test_update_field_literal_generates_context_assignment(self, tmp_path):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE",
                to_state="ACTIVE",
                event="START",
                actions=["set ctx.counter to 5"],
            ),
        ])
        schema = ProtocolSchema(
            protocol_name="demo",
            source_document="test.pdf",
            state_machines=[sm],
        )

        result = generate_code(schema, str(tmp_path))
        source_text = ""
        for path in result.files:
            if path.endswith("_sm_testsm.c"):
                source_text = open(path, encoding="utf-8").read()
                break

        assert "ctx->counter = 5;" in source_text
        assert result.generated_action_count == 1

    def test_unaligned_action_degrades_but_aligned_action_still_generates(self, tmp_path):
        sm = _make_sm(transitions=[
            ProtocolTransition(
                from_state="IDLE",
                to_state="ACTIVE",
                event="START",
                actions=["set ctx.counter to 1", "start hold timer"],
            ),
        ])
        fsm_ir = lower_state_machine_to_fsm_ir(sm, "demo")
        schema = ProtocolSchema(
            protocol_name="demo",
            source_document="test.pdf",
            state_machines=[sm],
            fsm_irs=[fsm_ir],
            state_contexts=[
                StateContextIR(
                    context_id="demo_context",
                    name="Demo Context",
                    canonical_name="demo_context",
                    scope="session",
                    state_field="session_state",
                    fields=[
                        ContextFieldIR(
                            field_id="demo.session_state",
                            name="Session State",
                            canonical_name="session_state",
                            type_kind="enum",
                            semantic_role="state",
                        ),
                        ContextFieldIR(
                            field_id="demo.counter",
                            name="Counter",
                            canonical_name="counter",
                            type_kind="u32",
                        ),
                    ],
                    timers=[],
                    resources=[],
                    invariants=[],
                )
            ],
        )

        result = generate_code(schema, str(tmp_path))
        source_text = ""
        for path in result.files:
            if path.endswith("_sm_testsm.c"):
                source_text = open(path, encoding="utf-8").read()
                break

        assert "ctx->counter = 1;" in source_text
        assert "typed degraded: start_timer(hold)" in source_text
        assert result.typed_action_count == 2
        assert result.generated_action_count == 1
        assert result.degraded_action_count == 1


# ── ref_source inference ──────────────────────────────────────


class TestRefSourceInference:
    def test_dotted_ctx_prefix_guard(self):
        guard = _try_parse_guard("ctx.state == IDLE")
        assert guard is not None
        assert guard.ref_source == "ctx"

    def test_dotted_msg_prefix_guard(self):
        guard = _try_parse_guard("msg.flags == 1")
        assert guard is not None
        assert guard.ref_source == "msg"

    def test_dotted_ctx_prefix_action(self):
        action = _try_parse_action("set ctx.counter to 5")
        assert action is not None
        assert action.ref_source == "ctx"

    def test_undotted_field_defaults_to_ctx(self):
        guard = _try_parse_guard("counter == 0")
        assert guard is not None
        assert guard.ref_source == "ctx"

    def test_protocol_namespace_guard_defaults_to_ctx(self):
        guard = _try_parse_guard("bfd.SessionState == Up")
        assert guard is not None
        assert guard.ref_source == "ctx"

    def test_protocol_namespace_action_defaults_to_ctx(self):
        action = _try_parse_action("set SND.NXT to 100")
        assert action is not None
        assert action.ref_source == "ctx"
