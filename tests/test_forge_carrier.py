import pytest

pytest.importorskip("forge_tool")
pa = pytest.importorskip("pyarrow")

from forge_tool import (  # noqa: E402 -- optional SDK must be available first
    TOOL_ENDPOINT_PROTOCOL,
    ToolEndpointDescriptor,
    ToolOperationDescriptor,
    make_registration_envelope,
)

from operator_vlm_policy._vendor.dora_binding import (  # noqa: E402
    tool_envelope_to_message,
    tool_message_to_envelope,
)
from operator_vlm_policy._vendor.tool_message import (  # noqa: E402
    ToolMessage,
    ToolMessageSizeError,
)


def registration():
    return make_registration_envelope(
        ToolEndpointDescriptor(
            protocol_version=TOOL_ENDPOINT_PROTOCOL,
            endpoint_id="reasoning.vlm",
            operations=(
                ToolOperationDescriptor(name="infer", semantics="action", status_supported=True),
            ),
        ),
        endpoint_instance_id="instance-1",
        request_id="registration-1",
    )


@pytest.mark.parametrize("container", ["batch", "table", "struct", "ipc"])
def test_public_forge_envelope_roundtrips_without_full_image_package(container):
    envelope = registration()
    batch = tool_envelope_to_message(envelope).to_arrow()
    value = batch
    if container == "table":
        value = pa.Table.from_batches([batch])
    elif container == "struct":
        value = pa.StructArray.from_arrays(batch.columns, fields=list(batch.schema))
    elif container == "ipc":
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, batch.schema) as writer:
            writer.write_batch(batch)
        value = sink.getvalue().to_pybytes()
    assert tool_message_to_envelope(ToolMessage.from_arrow(value)) == envelope


def test_carrier_rejects_oversized_payload_before_parsing():
    batch = tool_envelope_to_message(registration()).to_arrow()
    with pytest.raises(ToolMessageSizeError):
        ToolMessage.from_arrow(batch, max_payload_json_bytes=1)
