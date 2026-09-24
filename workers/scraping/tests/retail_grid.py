"""An Amazon-style search results grid (utility classes, aria-label ratings, review counts, badges, coupons, delivery
 notes, stock warnings, split screen-reader prices) for grid field detection tests."""


def grid(n: int = 16) -> str:
    cards = []
    for i in range(n):
        badge = "<span class='a-badge-text'>Best Seller</span>" if i % 5 == 0 else ""
        coupon = f"<span class='s-coupon-highlight-color'>Save {5 + i % 3}% with coupon</span>" if i % 3 == 0 else ""
        cards.append(
            f"<div data-component-type='s-search-result' data-asin='B0{i:08d}' class='s-result-item'><div class='s-card'>"
            f"<img class='s-image' src='https://m.media.test/images/{i}.jpg' alt='Widget model {i} in blue'>"
            f"{badge}<h2 class='a-size-mini'><a class='a-link-normal' href='/dp/B0{i:08d}'><span class='a-size-medium a-color-base a-text-normal'>Widget model {i}, {10 + i} pack</span></a></h2>"
            f"<div class='a-row a-size-small'><span aria-label='{3 + (i % 20) / 10:.1f} out of 5 stars'><i class='a-icon a-icon-star-small'><span class='a-icon-alt'>{3 + (i % 20) / 10:.1f} out of 5 stars</span></i></span>"
            f"<span aria-label='{1000 + i * 37:,} ratings'><span class='a-size-base s-underline-text'>({1000 + i * 37:,})</span></span></div>"
            f"<div class='a-row'><span class='a-size-base a-color-secondary'>{50 + i}+ bought in past month</span></div>"
            f"<div class='a-row'><a class='a-link-normal s-no-hover' href='/dp/B0{i:08d}'><span class='a-price'><span class='a-offscreen'>${19 + i}.99</span>"
            f"<span aria-hidden='true'><span class='a-price-whole'>{19 + i}.</span><span class='a-price-fraction'>99</span></span></span>"
            f"<span class='a-price a-text-price'><span class='a-offscreen'>${29 + i}.99</span></span></a></div>{coupon}"
            f"<div class='a-row'><span aria-label='FREE delivery Thu, Oct {i % 20 + 1}'>FREE delivery <span class='a-text-bold'>Thu, Oct {i % 20 + 1}</span></span></div>"
            f"<div class='a-row'><span class='a-color-price'>Only {i % 7 + 1} left in stock - order soon.</span></div>"
            f"<button class='a-button'>Add to cart</button></div></div>")
    return "<html><head><title>Results</title></head><body><div class='s-main-slot'>" + "".join(cards) + "</div></body></html>"
