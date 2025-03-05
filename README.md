# Common Knowledge Base

The Common Knowledge Base (CKB) is essentially a database management module where data is collected from public sector websites via scraping or API connection, and also by manually uploading files. It is important for the CKB that the collected data is up-to-date - therefore, the application also performs automatic periodic data updates. During the update, the information on the websites is compared with the data in the database and the data is updated only if there are changes. In the CKB, new API connections can be created and new websites can be added to scrape their content.

The collected data is used as a knowledge base for preparing responses to queries sent to the large language model via Bürokratt. Therefore, in addition to collecting data, it is important to make this data more readable for the language model by cleaning the data of excess (e.g. different css styles and html tags).

All development work related to the CKB must comply with the Architectural Decision Records' (ADR) requirements, which can be found at [https://ADR-Data-pipelines](https://github.com/buerokratt/Buerokratt-onboarding/tree/main/Architectural-Decision-Records-ADR/data-storage/data-pipelines).
