import pytest

from core.document import DesignDocument, DocumentError, empty


def doc_with_box():
    d = empty("t")
    d["part"] = {"source": "plate.step",
                 "ports": [{"id": "face_1", "frame": {"origin": [0, 0, 0], "normal": [1, 0, 0], "up": [0, 0, 1]}}]}
    d["elements"] = [{"id": "box_1", "type": "dummy_box", "params": {"length": 50.0}}]
    d["connections"] = [{"id": "c1", "from": {"instance": "box_1", "port": "outlet"},
                         "to": {"instance": "part", "port": "face_1"}, "joint": "part"}]
    return d


def test_empty_is_valid():
    DesignDocument()


def test_schema_error_is_reported():
    d = empty()
    d["units"]["length"] = "inch"
    with pytest.raises(DocumentError):
        DesignDocument(d)


@pytest.mark.parametrize("mutate", [
    lambda d: d["elements"].append(dict(d["elements"][0])),                  # duplicate id
    lambda d: d["connections"][0]["to"].update(port="face_9"),               # no such part port
    lambda d: d["connections"][0]["from"].update(instance="ghost"),          # no such element
    lambda d: d["connections"][0].update(joint="butt"),                      # part joint mismatch
    lambda d: d["elements"][0].update(id="part"),                            # reserved id
])
def test_reference_errors(mutate):
    d = doc_with_box()
    mutate(d)
    with pytest.raises(DocumentError):
        DesignDocument(d)


def test_edit_undo_redo():
    doc = DesignDocument(doc_with_box())
    doc.set_param("box_1", "length", 80.0)
    doc.set_param("box_1", "width", 12.0)
    assert doc.element("box_1")["params"] == {"length": 80.0, "width": 12.0}
    assert doc.undo()
    assert doc.element("box_1")["params"] == {"length": 80.0}
    assert doc.undo()
    assert doc.element("box_1")["params"] == {"length": 50.0}
    assert not doc.undo()
    assert doc.redo() and doc.element("box_1")["params"] == {"length": 80.0}


def test_invalid_edit_leaves_document_unchanged():
    doc = DesignDocument(doc_with_box())
    before = doc.to_json()
    with pytest.raises(DocumentError):
        doc.apply([{"op": "replace", "path": "/connections/0/joint", "value": "butt"}])
    assert doc.to_json() == before and not doc.can_undo


def test_new_edit_clears_redo():
    doc = DesignDocument(doc_with_box())
    doc.set_param("box_1", "length", 60.0)
    doc.undo()
    doc.set_param("box_1", "length", 70.0)
    assert not doc.can_redo


def test_save_load_round_trip(tmp_path):
    doc = DesignDocument(doc_with_box())
    doc.save(tmp_path / "d.json")
    again = DesignDocument.load(tmp_path / "d.json")
    assert again.data == doc.data
    assert again.part_path() == (tmp_path / "plate.step").resolve()
