from job_hunt.job_sources.html_to_text import html_to_text


def test_paragraphs_and_list_items_become_lines():
    html = "<p>About us</p><ul><li>Python</li><li>SQL</li></ul><p>Apply&nbsp;now</p>"
    assert html_to_text(html) == "About us\n\n- Python\n\n- SQL\n\nApply now"


def test_doubly_escaped_html_is_unescaped_first():
    # how Greenhouse sends advert content
    escaped = "&lt;p&gt;Graduate &amp;amp; intern roles&lt;/p&gt;"
    assert html_to_text(escaped, is_escaped=True) == "Graduate & intern roles"


def test_scripts_and_styles_are_dropped():
    assert html_to_text("<style>p{}</style><p>Role</p><script>track()</script>") == "Role"


def test_empty_input_gives_none():
    assert html_to_text(None) is None
    assert html_to_text("<p> </p>") is None
