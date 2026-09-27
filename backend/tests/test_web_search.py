from app.services.web_search import is_excluded_domain


def test_excludes_known_aggregator_domains():
    assert is_excluded_domain("https://www.yelp.com/biz/some-restaurant")
    assert is_excluded_domain("https://tripadvisor.co.uk/Restaurant_Review")
    assert is_excluded_domain("https://linktr.ee/somerestaurant")
    assert is_excluded_domain("http://m.facebook.com/somerestaurant")


def test_allows_real_restaurant_domains():
    assert not is_excluded_domain("https://www.biocenter.es")
    assert not is_excluded_domain("https://restaurantsesamo.com/menu")


def test_subdomain_matching_does_not_false_positive_on_lookalikes():
    # "notyelp.com" should not match "yelp.com" via naive substring matching
    assert not is_excluded_domain("https://www.notyelp.com")
