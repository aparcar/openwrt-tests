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
          select($devices[.] != null and $devices[.].snapshots_only != true) |
          {
            "device": .,
            "proxy": $labs[$lab].proxy,
            "target": $devices[.].target,
            "firmware": $devices[.].firmware,
            "maintainers": $labs[$lab].maintainers,
            "snapshots_only": ($devices[.].snapshots_only // false)
          }
        )
    )
  | flatten
' labnet.yaml
