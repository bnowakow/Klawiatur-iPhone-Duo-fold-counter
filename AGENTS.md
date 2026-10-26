# Data corrections

After every data correction, always run `make sheets-sync` to synchronize the
corrected database data to Google Sheets. Run it after the correction has committed
and verify that the sync succeeds before reporting the work complete. If the sync
fails, report the failure and any remaining blocker explicitly.
