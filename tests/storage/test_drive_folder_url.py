import pytest

from plugin.storage.drive_url import parse_google_drive_folder_url


def test_imports_google_folder_id_without_fetching_url_or_capturing_query_credentials():
    imported = parse_google_drive_folder_url(
        "https://drive.google.com/drive/u/0/folders/AbC_123-xyz?usp=sharing&resourcekey=secret-key"
    )

    assert imported == "AbC_123-xyz"


@pytest.mark.parametrize(
    "url",
    [
        "http://drive.google.com/drive/folders/AbC_123-xyz",
        "https://drive.google.com.attacker.invalid/drive/folders/AbC_123-xyz",
        "https://drive.google.com/drive/files/AbC_123-xyz",
        "https://user:password@drive.google.com/drive/folders/AbC_123-xyz",
        "https://drive.google.com:8443/drive/folders/AbC_123-xyz",
    ],
)
def test_drive_folder_url_import_rejects_unsafe_or_non_folder_urls(url):
    with pytest.raises(ValueError):
        parse_google_drive_folder_url(url)
