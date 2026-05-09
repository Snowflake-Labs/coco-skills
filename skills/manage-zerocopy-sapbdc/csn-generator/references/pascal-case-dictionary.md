# SAP PascalCase Word Dictionary

Used for greedy longest-match splitting of ALL_UPPERCASE Snowflake column names into PascalCase.

## Algorithm

1. Convert the input to UPPERCASE for matching
2. Scan left-to-right, always matching the longest possible word first
3. For each match, emit the word in its canonical PascalCase form
4. If a substring cannot match any word, treat consecutive unmatched characters as a single token and capitalize only the first letter (e.g., `XYZFOO` where `XYZ` matches no word → `Xyz` + continue matching `FOO`)

## Dictionary (sorted by length descending)

### Multi-syllable domain words (match first)
`Configuration`, `Classification`, `Procurement`, `Consumption`, `Scheduling`, `Description`, `Transaction`, `Certification`, `Authorization`, `Determination`, `Denomination`, `Responsibility`, `Characteristic`, `Organization`, `Interoperation`, `Hierarchical`, `International`, `Communication`, `Manufacturing`, `Distribution`, `Relationship`, `Replacement`, `Requirement`, `Procurement`, `Availability`, `Notification`, `Subscription`, `Cancellation`, `Confirmation`, `Abbreviation`, `Aggregation`, `Customizing`, `Processing`, `Purchasing`, `Production`, `Accounting`, `Profitability`, `Valuating`, `Controlling`, `Engineering`, `Settlement`, `Evaluation`, `Warehouse`, `Hierarchy`, `Tolerance`, `Reference`, `Operation`, `Condition`, `Indicator`, `Relevance`, `Valuation`, `Costing`, `Storage`, `Planning`, `Category`, `External`, `Internal`, `Standard`, `Material`, `Customer`, `Supplier`, `Delivery`, `Purchase`, `Division`, `Calendar`, `Currency`, `Duration`, `Interval`, `Strategy`, `Security`, `Quantity`, `Regional`, `National`, `Language`, `Industry`, `Business`, `Property`, `Deletion`, `Required`, `Relevant`, `Allowed`, `Percent`, `Profile`, `Country`, `Version`, `Company`, `Factory`, `Product`, `Account`, `Control`, `Content`, `Network`, `Section`, `Segment`, `General`, `Special`, `Maximum`, `Minimum`, `Default`, `Pricing`, `Posting`, `Leading`, `Overall`, `Forward`, `Packing`, `Loading`, `Decimal`, `Blocked`

### Common SAP abbreviated words
`Crcy`, `Qty`, `Amt`, `Cat`, `Desc`, `Auth`, `Acct`, `Addr`, `Curr`, `Attr`, `Cert`, `Char`, `Cmpny`, `Cnfmn`, `Cntrl`, `Detn`, `Distbn`, `Grp`, `Mfr`, `Mfg`, `Org`, `Plnt`, `Prdctn`, `Prfl`, `Schd`, `Stge`, `Supl`, `Tol`, `Whse`

### Core SAP words
`Vendor`, `Region`, `Marked`, `Active`, `Source`, `Target`, `Amount`, `Fiscal`, `Period`, `Record`, `Volume`, `Weight`, `Sector`, `Serial`, `Output`, `System`, `Config`, `Bridge`, `Custom`, `Domain`, `Format`, `Global`, `Header`, `Import`, `Export`, `Incoterms`, `Credit`, `Change`, `Safety`, `Supply`, `Profit`, `Center`, `Object`, `Module`, `Return`, `Ledger`, `Method`, `Season`, `Origin`, `Actual`, `Budget`, `Market`, `Batch`, `Brand`, `Range`, `Stock`, `Shelf`, `Limit`, `Label`, `Issue`, `Level`, `Chain`, `Point`, `Input`, `Layer`, `Entry`, `Model`, `Scope`, `Shift`, `Phase`, `Route`, `Depot`, `Queue`, `Usage`, `Basic`

### Short SAP words
`Sales`, `Order`, `Plant`, `Price`, `Group`, `Class`, `Valid`, `Value`, `Field`, `Block`, `Start`, `Color`, `Cross`, `Fixed`, `Gross`, `Lower`, `Upper`, `Inter`, `Multi`, `Total`, `Check`, `Asset`, `Prior`, `Ratio`

### Atomic SAP words (2-4 chars, match last)
`UUID`, `Date`, `Time`, `User`, `Code`, `Type`, `Text`, `Name`, `Unit`, `Area`, `Rule`, `Item`, `Rate`, `Role`, `Size`, `Kind`, `Mode`, `Rank`, `Tier`, `Step`, `Flow`, `Link`, `Lot`, `Key`, `MRP`, `BOM`, `EAN`, `SKU`, `OID`, `Tax`, `Net`, `Set`, `Per`, `Num`, `Ind`, `Cat`, `GR`, `GI`, `PO`, `SO`, `WM`, `ID`, `In`, `Is`, `To`, `By`, `No`, `Of`, `At`, `On`
