"""Read the solid names of a STEP assembly exactly as written.

build123d's import_step rewrites names (" .()" become "_"), which would turn
"gating.runner_1" into "gating_runner_1"; the bridge reads OCCT's XCAF labels
directly instead.
"""
from __future__ import annotations

from pathlib import Path

from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.collections import Sequence_TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFDoc import XCAFDoc_DocumentTool


def _name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return TCollection_AsciiString(attr.Get()).ToCString()
    return ""


def read_step_names(path: str | Path) -> dict[str, list[str]]:
    """{assembly name: [component names]} for the free shapes of a STEP file."""
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.Transfer(doc):
        raise ValueError(f"cannot read STEP {path}")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = Sequence_TDF_Label()
    tool.GetFreeShapes(free)
    out = {}
    for i in range(1, free.Length() + 1):
        top = free.Value(i)
        comps = Sequence_TDF_Label()
        tool.GetComponents_s(top, comps)
        names = []
        for j in range(1, comps.Length() + 1):
            ref = TDF_Label()
            c = comps.Value(j)
            names.append(_name(ref) if tool.GetReferredShape_s(c, ref) else _name(c))
        out[_name(top)] = names
    return out
