import pytest

from plugin.working_files.service import requires_intermediate_opt_in, validate_file_type


@pytest.mark.parametrize("label", ["working", "Tracking Data", "camera-data_v2", "A" * 32])
def test_custom_file_type_label_accepts_bounded_human_names(label):
    assert validate_file_type(label) == label


@pytest.mark.parametrize("label", [None, "", "../outside", "type\\escape", "line\nbreak", "A" * 33])
def test_custom_file_type_label_rejects_path_controls_and_unbounded_values(label):
    with pytest.raises(ValueError, match="Working file type"):
        validate_file_type(label)


@pytest.mark.parametrize("label", ["cache", "Cache", "intermediate render", "Intermediate-Render", "simulation"])
def test_custom_labels_cannot_bypass_explicit_intermediate_opt_in(label):
    assert requires_intermediate_opt_in(label)
