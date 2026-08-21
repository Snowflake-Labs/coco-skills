# PNDSPY1008

pandas.core.series.Series.hist

Message: pandas.core.series.Series.hist has a partial mapping, Snowpark pandas does not yet support the `bins` parameter with types other than `int`.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.series.Series.hist`](https://pandas.pydata.org/docs/reference/api/pandas.Series.hist.html) usage.
Snowpark pandas does not yet support the `bins` parameter with types other than 'int'

## Scenario

**Input**

The example below illustrates the not supported use of `pandas.core.series.Series.hist`.

```python
import pandas as pd

data = pd.Series([[1.2, -0.5, 0.3, 2.1, -2.2, 1.7, 0.0, -1.1, 2.5, -2.8]])
custom_bins = [-3, -2, -1, 0, 1, 2, 3]
data.hist(bins=custom_bins)
plt.xlabel('Value')
plt.ylabel('Frequency')
plt.show()
```

**Output**

The SMA adds the EWI `PNDSPY1008` to the output code to indicate that in one scenario is not supported in Snowpark pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

data = pd.Series([[1.2, -0.5, 0.3, 2.1, -2.2, 1.7, 0.0, -1.1, 2.5, -2.8]])
custom_bins = [-3, -2, -1, 0, 1, 2, 3]
#EWI: PNDSPY1008 => pandas.core.series.Series.hist has a partial mapping, Snowpark pandas does not yet support the `bins` parameter with types other than `int`.
data.hist(bins=custom_bins)
plt.xlabel('Value')
plt.ylabel('Frequency')
plt.show()
```

**Recommended fix**

Requires a manual change by using the numpy [`digitize`](https://numpy.org/doc/stable/reference/generated/numpy.digitize.html)
function, for this you will need to import numpy and replace the `pd.Series` for `np.array` to be able to use `digitize`,
after that, you need to count the frequencies for each bin and create labels for the customs bins.
Finally, you can use `plt.bar` to plot the histogram with custom labels.
Here is the above output code with the fix:

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd
import numpy as np

data = np.array([[1.2, -0.5, 0.3, 2.1, -2.2, 1.7, 0.0, -1.1, 2.5, -2.8]])
custom_bins = [-3, -2, -1, 0, 1, 2, 3]
bin_indices = np.digitize(data, custom_bins, right=False)

# Count frequencies for each bin
counts = [np.sum(bin_indices == i) for i in range(1, len(custom_bins))]

# Create string labels for each bin
bin_labels = [f"({custom_bins[i - 1]}, {custom_bins[i]})" for i in range(1, len(custom_bins))]

plt.bar(bin_labels, counts, edgecolor='black', alpha=0.7)
plt.xlabel('Value')
plt.ylabel('Frequency')
plt.show()
```


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
