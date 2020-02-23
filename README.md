Given data from <https://opendata.blender.org/snapshots/>,
figure out which is fastest of:

- AMD Ryzen 5 3500U
- Radeon Vega 8 Mobile

Data is taken from the `opendata-*.zip` file.

# Instructions

1. Update `DEVICE1` and `DEVICE2` at the top of [`wrangle.py`](wrangle.py)
1. `./wrangle.py`

`wrangle.py` will now:

- Figure out which devices you actually mean
- Find runs that have been done on both devices, with other parameters being the same:
  - Blender version
  - OS
  - Scene name
- Compare performance of the two devices and print a summary number
