# Use the current stint's average, matching the website's average-rate metric.
# Diagnostic estimates on stale or uncertain reports are not valid comparisons.
def fpm: (. * 100 | round) / 100 | tostring;
def blue_label: "\u001b[34m" + . + "\u001b[0m";
def pad_right($width): . + (" " * ($width - length));

if type != "object" or (.status | type) != "string" then
  error("Invalid comparison report: missing status")
elif .status != "compared" then
  "\("Website (ipduotest.pl):" | pad_right(24) | blue_label)  unavailable",
  "\("Our current person (avg):" | pad_right(24) | blue_label)  unavailable",
  "\("Difference (ours − site):" | pad_right(24) | blue_label)  unavailable",
  "Comparison unavailable: \(.status | gsub("_"; " "))\(if .details.error then ". " + .details.error else "" end)"
elif (.official_fpm | type) != "number" or (.stint_fpm | type) != "number" then
  error("Pace comparison unavailable: missing folds/minute values")
else
  (.stint_fpm - .official_fpm) as $gap |
  [
    {label: "Website (ipduotest.pl):", value: (.official_fpm | fpm)},
    {label: "Our current person (avg):", value: (.stint_fpm | fpm)},
    {label: "Difference (ours − site):", value: ((if $gap > 0 then "+" else "" end) + ($gap | fpm))}
  ] as $rows |
  ($rows | map(.label | length) | max) as $label_width |
  ($rows | map(.value | length) | max) as $value_width |
  $rows[] |
  "\(.label | pad_right($label_width) | blue_label)  \(.value | pad_right($value_width)) folds/min"
end
