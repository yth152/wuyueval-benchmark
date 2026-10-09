"""Incremental OpenAI SSE and Qwen leading <think> tag parsing."""
import json


async def sse_data(lines):
    data = []
    async for line in lines:
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield "\n".join(data)


def event(kind, **values):
    return "data: " + json.dumps({"type": kind, **values}, ensure_ascii=False) + "\n\n"


class ThinkSplitter:
    """Only recognize a leading explicit <think> block, never infer thoughts."""
    def __init__(self):
        self.mode = "start"
        self.pending = ""
        self.tag_seen = False

    def feed(self, text):
        self.pending += text
        result = []
        if self.mode == "start":
            candidate = self.pending.lstrip()
            if not candidate or ("<think>".startswith(candidate) and candidate != "<think>"):
                return result
            if candidate.startswith("<think>"):
                self.pending = candidate[7:]
                self.mode = "reasoning"
                self.tag_seen = True
            else:
                self.mode = "content"
        if self.mode == "reasoning":
            ending = "</think>"
            if ending in self.pending:
                thought, self.pending = self.pending.split(ending, 1)
                if thought:
                    result.append(("reasoning", thought))
                self.mode = "content"
            else:
                keep = max((n for n in range(1, len(ending))
                            if self.pending.endswith(ending[:n])), default=0)
                ready = self.pending[:-keep] if keep else self.pending
                self.pending = self.pending[-keep:] if keep else ""
                if ready:
                    result.append(("reasoning", ready))
                return result
        if self.mode == "content" and self.pending:
            result.append(("content", self.pending))
            self.pending = ""
        return result

    def finish(self):
        result = []
        if self.pending:
            result.append(("reasoning" if self.mode == "reasoning" else "content", self.pending))
            self.pending = ""
        return result

