yq -o=json '
  . as $root |
  $root.labs as $labs |
  $root.devices as $devices |
  $labs
  | to_entries
  | map(
      .key as $lab |
      .value.devices
      | map(
          select($devices[.] != null) |
          {
            "device": .,
            "proxy": $labs[$lab].proxy,
            "target": $devices[.].target,
            "firmware": $devices[.].firmware,
            "maintainers": $labs[$lab].maintainers

          }
        )
    )
| flatten
' labnet.yaml
