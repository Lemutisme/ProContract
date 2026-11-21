"""Join native requests to actors without conflating a session with its threads.

This is an observation helper, not a source of execution authority or a role
classifier. Callers supply roles from native launch records or delivered policy.
"""

from collections import defaultdict
import re


class ActorIndex:
    def __init__(self):
        self.threads = {}
        self.sessions = defaultdict(set)

    def add(self, thread_id, session_id, descriptor):
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("native thread identity is required")
        if thread_id in self.threads and self.threads[thread_id] != descriptor:
            raise ValueError(f"conflicting native thread identity: {thread_id}")
        self.threads[thread_id] = descriptor
        if session_id:
            self.sessions[session_id].add(thread_id)

    def resolve(self, headers):
        thread = headers.get("thread-id")
        if thread:
            # An unknown explicit thread must not borrow its parent's identity.
            return self.threads.get(thread), "exact_thread" if thread in self.threads else "unknown_thread"
        candidates = self.sessions.get(headers.get("session-id"), set())
        if len(candidates) == 1:
            return self.threads[next(iter(candidates))], "unique_session"
        return None, "ambiguous_session" if candidates else "unknown_session"


_PACKET = re.compile(r"<external_pro_contract_packet_(\d+)>(.*)</external_pro_contract_packet_\1>", re.S)


def ordered_worker_packet(texts):
    """Reassemble contiguous observed packet parts for literal policy comparison.

    Input must come from native user-message records, never task files or tool
    output. Gaps or duplicate chunks do not establish a delivered policy. A
    contiguous prefix alone does not establish that the full packet was received.
    """
    parts = {}
    for text in texts:
        match = _PACKET.fullmatch(text)
        if match:
            index = int(match[1])
            if index in parts:
                return None
            parts[index] = match[2]
    if not parts or sorted(parts) != list(range(len(parts))):
        return None
    return "".join(parts[index] for index in sorted(parts))


def native_contexts(items, prefix="pro_contract_packet", source="pro_contract_worker"):
    """Decode observed context representations, preserving their input coordinates.

    The inline envelope and fragmented packet are separate input contexts. For
    a fragmented worker the inline envelope usually contains transport guidance,
    not the packet. Never combine it with fragments or substitute it for missing
    fragments. Source labels describe the wire representation, not authenticity,
    role authority, model use or completeness of a retained history.
    """
    label = re.compile(re.escape(prefix) + r"_(\d+)")
    wrapped = re.compile(r"<external_" + re.escape(prefix) + r"_(\d+)>(.*)</external_" + re.escape(prefix) + r"_\1>", re.S)
    inline = re.compile(r'<codex_internal_context source="' + re.escape(source) + r'">\n(.*)\n</codex_internal_context>', re.S)
    parts, envelopes = [], []
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        candidates = []
        if (item.get("type") == "function_call_output" and item.get("namespace") == "agent"
                and item.get("call_id") is None and isinstance(item.get("output"), str)):
            match = label.fullmatch(item.get("name") or "")
            if match:
                candidates.append((int(match[1]), item["output"], None, "named_fragment"))
        elif item.get("type") == "message" and item.get("role") == "user":
            for content_index, content in enumerate(item.get("content", [])):
                if not isinstance(content, dict) or content.get("type") != "input_text" or not isinstance(content.get("text"), str):
                    continue
                text = content["text"]
                match = wrapped.fullmatch(text)
                if match:
                    candidates.append((int(match[1]), match[2], content_index, "legacy_fragment"))
                match = inline.fullmatch(text)
                if match:
                    envelopes.append({"representation": "inline", "source_label": source,
                        "state": "observed", "coverage": "closed_inline_envelope", "text": match[1],
                        "records": [{"item_index": item_index, "content_index": content_index,
                                     "item_type": "message", "role": "user"}]})
        for index, text, content_index, representation in candidates:
            record = {"item_index": item_index, "part_index": index, "representation": representation,
                      "item_type": item["type"]}
            record.update({key: item[key] for key in ("role", "namespace", "name") if key in item})
            if content_index is not None:
                record["content_index"] = content_index
            parts.append((index, text, record))
    contexts = []
    if parts:
        parts.sort(key=lambda part: part[0])
        indices = [part[0] for part in parts]
        state = "ambiguous" if len(set(indices)) != len(indices) else "incomplete" if indices != list(range(len(indices))) else "observed"
        contexts.append({"representation": "fragments", "source_label": prefix,
                         "state": state, "coverage": "contiguous_prefix_without_end_marker" if state == "observed" else "unknown",
                         "text": "".join(part[1] for part in parts) if state == "observed" else None,
                         "records": [part[2] for part in parts]})
    return contexts + envelopes


def native_context_packet(items, prefix="pro_contract_packet"):
    """Compatibility view: packet when present, otherwise one inline worker context.

    Duplicate/gapped fragments never fall back to an inline transport instruction.
    Multiple inline contexts are not merged or silently selected. A contiguous
    packet prefix does not establish that its unmarked end was retained.
    """
    contexts = native_contexts(items, prefix)
    if contexts and contexts[0]["representation"] == "fragments":
        return contexts[0]["text"]
    return contexts[0]["text"] if len(contexts) == 1 else None
