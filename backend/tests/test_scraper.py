from app.services.scraper import _html_text, find_menu_link, find_testimonial_link

HOMEPAGE_HTML = """
<html>
<head><script>var x = 1;</script></head>
<body>
  <nav>Home | About</nav>
  <header>Restaurant Sesamo</header>
  <p>Welcome to our restaurant in Barcelona.</p>
  <a href="/carta">Carta</a>
  <a href="/opiniones">Opiniones de clientes</a>
  <a href="/contact">Contact</a>
  <footer>Copyright 2024</footer>
</body>
</html>
"""


def test_find_menu_link_matches_spanish_menu_keyword():
    link = find_menu_link("https://example.com", HOMEPAGE_HTML)
    assert link == "https://example.com/carta"


def test_find_testimonial_link_matches_spanish_reviews_keyword():
    link = find_testimonial_link("https://example.com", HOMEPAGE_HTML)
    assert link == "https://example.com/opiniones"


def test_find_menu_link_returns_none_when_absent():
    html = "<html><body><a href='/about'>About</a></body></html>"
    assert find_menu_link("https://example.com", html) is None


def test_html_text_strips_script_and_nav_boilerplate():
    text = _html_text(HOMEPAGE_HTML)
    assert "var x = 1" not in text
    assert "Home | About" not in text
    assert "Copyright 2024" not in text
    assert "Welcome to our restaurant in Barcelona." in text
