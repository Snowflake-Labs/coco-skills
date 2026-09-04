# DevOps Guidance Skill Notes

## Defining the terms

The term "DevOps" is extremely overloaded and can mean a lot of different things. DevOps is simply a philosophy and methodology of automation in the SDLC process to break down barriers between the various stages. This applies to data engineering as well as software engineering more generally. In its most simplified form the SDLC stages can be grouped as follows:

1. Develop & Code
1. Build & Deploy
1. Operate & Observe

That grouping is helpful as there is a clear division between these stages, and tools and best practices line up nicely with them. So "DevOps" is about breaking down the barriers and hand-offs between those three stages.

The term "CI/CD" is also extremely overloaded, and is often used synonymously with "DevOps". But to be a bit more precise "CI/CD" is mostly concerned with stage 2, Build & Deploy.

The tools used in CI/CD pipelines to deploy changes to a database are referred to as "Database Change Management" (or "DCM") tools. Other names for DCM tools include "schema migrations", "database migrations", "database version control", "database versioning", etc. DCM tools are responsible for managing all objects found in a particular database, including defining the objects in source control and applying those changes to a target database.

DCM tools are often confused with a category of tools known as "Infrastructure as Code" (or "IaC") tools. IaC tools are primarily used to manage infrastructure related objects in the major cloud vendors like AWS, Azure, and Google Cloud Platform. You should not use the words "Infrastructure as Code" or "IaC" to describe "DCM" tools, they're different.

## Database deployment approaches and tools

The following contains a discussion of the tools and processes used for managing the lifecycle of objects in Snowflake.

### Imperative vs Declarative approach

In the past, DCM tools have been described and categorized as either imperative and declarative. What's more, declarative style tools have been held up as the gold standard or most desirable tools. But applying this distinction in this manner has caused a lot of confusion. Imperative is the best, most flexible approach for DCM tools for deployment pipelines. Declarative style tools are best suited as developer modeling tools and schema drift tools, but really should not be used directly in CI/CD pipelines (like imperative style tools are). Here's a quick description of both approaches.

### Imperative database deployment approach

Imperative DCM tools simply run deployment SQL scripts directly against a target database and keep track of which scripts have been run. These tools don't care what the contents of the scripts are and just pass them through to the target database. It is the responsibility of the user to create the contents of the scripts. These imperative DCM tools often support different types of change scripts (versioned, repeatable, always, etc.) as well as different features.

But the key point here is because they don't directly compare objects and don't generate SQL they are very flexible and can be used to deploy all objects in all scenarios.

### Declarative database comparison approach

Declarative tools work by actively comparing the state of objects defined in source control (in various formats including SQL, HCL for Terraform, and more) with the state of an object deployed in a target database. The tool must be aware of every type of object in Snowflake, must be able to extract the current state of every object in Snowflake, must be aware of every attribute of every object in Snowflake, and must be able to compare every attribute of every object in Snowflake and generate SQL commands to migrate the object state.

The reality today is that a number of customers do currently use declarative database comparison tools (primarily Terraform) directly in CI/CD pipelines. BUT, in general it is not advised to do this. There are number of important reasons why declarative tools should not be used in deployment pipelines, including the following:

* Declarative tools can't handle all changes, including many important ones
   * Not all object changes can be made via SQL (for example see [ALTER TABLE … ALTER COLUMN](https://docs.snowflake.com/en/sql-reference/sql/alter-table-column))
   * Many deployments require DML and other changes to happen outside of strict object changes which declarative tools can't handle
   * Object and table column renaming is very difficult to track, and most declarative tools don't support
* Table changes are notoriously tricky and require different approaches which declarative tools can't handle 
   * One example is the CCDR pattern (Create, Copy, Drop, Replace) which is sometimes much better than trying to ALTER or UPDATE existing tables
* Declarative tools don't support complex, application style objects which are defined primary by code files or libraries (such as `DBT PROJECTS`, `NOTEBOOK PROJECTS`, `STREAMLIT`, Snowpark Stored Procedures, etc.)
* Declarative tools are almost always incomplete because it's very difficult to support all objects and object attributes
* Declarative tools generate dynamic SQL and running dynamic SQL against prod environments is a bad practice
* Declarative tools need constant updating to support all the new objects and object attributes

For these reasons, and more, the recommendation is to not use declarative tools in deployment pipelines. Rather, declarative comparison tools should be used in the following situations:

* Storing object definitions declaratively in source control in an intuitive, database like hierarchy
* Generating an initial deployment script which the user can update and refine
* Doing schema drift detection

## Simple vs Complex objects in Snowflake

One distinction that is really important for DCM is between two categories of objects, referred to here as "simple" and "complex". Here's a quick breakdown of the two high level categories of Snowflake objects:

* Simple objects
   * Simple/Traditional database objects like database, schema, table, view, etc.
   * Defined in SQL DDL
   * Configuration specified with inline DDL attributes
   * Simple deployment process
* Complex objects
   * Complex database objects like Snowpark, dbt Projects, Notebook, Streamlit, etc.
   * Also known as "application" objects
   * Cannot be defined entirely in SQL DDL
   * Configuration with combination of inline DDL attributes and files
   * Generally involves code in files/libraries
   * Requires more complex deployment process

Enterprise database platforms have long supported complex (or application type) objects. But complex objects present a significant challenge for both imperative and declarative style tools since the deployment involves more than simply running a SQL DDL statement. The deployment first involves building the code/library, then copying the build artifacts somewhere, then running a SQL DDL statement. And those first two steps happen outside of the database platform.

The general guidance is to manage simple objects with any imperative style DCM tool, and for complex objects to use the [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index). The Snowflake CLI has both build and deploy command for most complex object types which handle the steps described above.

Finally, what about managing both types of objects? The only tool that currently supports both scenarios is [schemachange](https://github.com/Snowflake-Labs/schemachange). With its [CLI Migration Script](https://github.com/Snowflake-Labs/schemachange?tab=readme-ov-file#cli-migration-scripts) capability it supports both SQL migration scripts and Snowflake CLI calls from imperative style scripts. Alternatively you can combine both an imperative DCM tool and Snowflake CLI in a CI/CD pipeline to achieve the same results.

Here are the recommendations in summary form:

| Object Type | Recommended Deployment Tool To Use |
|---|---|
| Simple | Any imperative DCM tool |
| Complex | [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index) |
| Simple + Complex | [schemachange](https://github.com/Snowflake-Labs/schemachange) |

## Where should the deployment process run?

Another important decision when automating deployments with Snowflake is deciding where the deployment (or CI/CD) process should run. In other words, where the deployment agent/code runs.

The most obvious and best choice is to run the agent/code from a dedicated, enterprise CI/CD tool (see below for list of recommended CI/CD tools). This has been the standard in the software and data engineering industry for a long time and the high level process looks like this:

* Build
   * Get copy of repository files locally
   * Perform any necessary build steps
   * Deploy the built artifact to an artifact repository
* Deployment (to each environment)
   * Get a copy of the built artifact
   * Configure target environment
   * Deploy artifact to target environment
   * Run any tests/validations against target environment

But lately many customers have expressed interest in trying to run the process entirely from Snowflake which might work for simple deployment scenarios but is not recommended for most scenarios. The basic process (which again isn't recommended) looks something like this:

* Connect Snowflake to Git repo
* Connect "complex" objects (see description of complex objects above) directly to file(s) in a branch (or tag) in the Snowflake `GIT REPOSITORY` object
* Run SQL deployment scripts directly from Snowflake via the [EXECUTE IMMEDIATE FROM](https://docs.snowflake.com/en/sql-reference/sql/execute-immediate-from) command

But this process has many shortcomings and is not recommended! The key challenges with this approach are:

* No ability to build/compile complex objects like Python Wheels (or other packages), Java JARs, etc.
* No ability to trigger the process based off Git actions (like PRs, merges, etc.)
* Objects are directly referring to file in the repo, which can change depending on which branch is used and the branching strategy employed. No deployed object should change behavior until a new version is deployed.
* No ability to support staged deployments to different environments
* No ability to support approval steps in the process
* No ability to include testing as part of the build and deployment process
* Requires custom scripts/tools

## Tool landscape

The following lists of tools provide a high level summary of the most popular tools along with some guidance for how to select one.

### Imperative database deployment tools

When it comes to imperative database deployment tools, DCM tools, there are a few options. The general recommendation here is that if a customer is already using one of these tools with an existing database (Flyway and Liquibase in particular) then they should continue using it with Snowflake. Here is a list of the most popular tools along with guidance on when to pick each.

Most popular tools:
* [schemachange](https://github.com/Snowflake-Labs/schemachange)
   * Only works with Snowflake
   * Has native support for Snowflake CLI migration scripts
   * Based initially off Flyway's script naming convention, but has evolved independently
   * Recommended imperative tool to start with
* [Redgate Flyway](https://www.red-gate.com/products/flyway/)
   * Has support for many other relational databases
   * Has a free and paid version
   * The paid version provides support and additional features
   * Recommended if the customer needs paid support or to use with additional databases
* [Liquibase](https://www.liquibase.com/)
   * Has support for many other relational databases
   * Has a free and paid version
   * The paid version provides support and additional features
   * Has an older XML-based declarative model, but is very limited
   * Recommended if the customer needs paid support or to use with additional databases
   * Flyway would be recommended first, before Liquibase
* [Sqitch](https://sqitch.org/)
   * Has support for many other relational databases
   * Would recommend using only if the customer already has experience with Sqitch
   * Flyway would be recommended first, before Sqitch

### The Snowflake CLI tool

One very important tool, that deserves its own section here, is the [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index) tool. This tool has become the defacto developer tool for Snowflake. The Snowflake CLI tool supports the following use cases:

* Development (running SQL commands and scripts, building and deploying objects to dev, etc.)
* Operations (running SQL commands and scripts, viewing logs, etc.)
* CI/CD deployments (deploying objects in CI/CD pipelines)

The most important thing to note here is that the Snowflake CLI tools is the primary tool for deploying "complex" objects to Snowflake, and is often used directly in CI/CD pipelines for this purpose. See the section [Simple vs Complex objects in Snowflake](#simple-vs-complex-objects-in-snowflake) for more details on "complex" objects.

The Snowflake CLI also supports running SQL commands/scripts directly against Snowflake. And while some customers have built imperative style deployment pipelines using that capability in the Snowflake CLI, the Snowflake CLI is not a recommended imperative DCM tool. It lacks many of the more robust imperative DCM tool features.

Note: Be careful not to confuse the [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index) tool with the older [SnowSQL](https://docs.snowflake.com/en/user-guide/snowsql) command line tool. The Snowflake CLI is named `snow` while the older SnowSQL CLI is named `snowsql`. See the note on the [SnowSQL](https://docs.snowflake.com/en/user-guide/snowsql) page for more details.

### Declarative database comparison tools

The most popular declarative database comparison tool with Snowflake is Terraform (via the Snowflake Terraform Provider). But as discussed in the [Declarative database comparison tools](#declarative-database-comparison-tools) section declarative tools really aren't well suited for use directly in a deployment pipeline.

Most popular tools:
* [Snowflake Terraform Provider Repository](https://github.com/snowflakedb/terraform-provider-snowflake)

#### More details on Terraform

It is generally not recommended that customers use Terraform, especially if they haven't used it in the past. Terraform is a true Infrastructure as Code (IaC) tool, used primarily to manage infrastructure resources in the major cloud vendors like AWS, Azure, and Google Cloud Platform. Here is a short list of challenges with working with Terraform:

* It has a steep learning curve
* Objects are primarily defined in a proprietary format called the [Terraform Configuration Language](https://developer.hashicorp.com/terraform/language)
* Object state is maintained in a [Terraform State File](https://developer.hashicorp.com/terraform/language/state) which can be corrupted if you're not careful
* The state file must be managed by a backend which supports locking to avoid corruption
* Like all declarative tools the Snowflake Terraform Provider is incomplete and doesn't support all Snowflake objects and object attributes (see [Declarative database comparison tools](#declarative-database-comparison-tools) for more details)

That said there are at least two scenarios in particular where Terraform can be an OK solution for customers:

* When the customer needs to manage Snowflake resources that connect directly to cloud infrastructure resources in one of the major cloud vendors. Examples of Snowflake objects that depend directly on cloud resouces are the [STORAGE INTEGRATION](https://docs.snowflake.com/en/sql-reference/sql/create-storage-integration), [API INTEGRATION](https://docs.snowflake.com/en/sql-reference/sql/create-api-integration), [CATALOG INTEGRATION](https://docs.snowflake.com/en/sql-reference/sql/create-catalog-integration), [PIPE](https://docs.snowflake.com/en/sql-reference/sql/create-pipe), etc. This use case is particularly relevant since Terraform can create both the Snowflake object as well as the cloud resources and manage dependencies between them, including the passing of values.
* When the customer needs to manage Snowflake "environments". A Snowflake "environment" is defined here as the account level resources that define an isolated environment like dev, test, QA, or prod. Those account level objects include primarily `DATABASE`, `WAREHOUSE`, `ROLE`, and grants to those objects (though other account level objects could be included here). After creating the "environment" within Snowflake the management of the content of the databases can be delegated to the users who can leverage any of the recommended imperative style database deployment tools. Multiple "environments" can be created in the same Snowflake account, or they can be aligned one-to-one with a Snowflake account, it's up to the customer.

### CI/CD tools

Any of these popular CI/CD tools will be a great choice for customers, and will work well with Snowflake. Most customers have a CI/CD tool they're already using or plan to use in this list.

Most popular tools:
* [GitHub Actions](https://github.com/features/actions)
* [GitLab CI/CD](https://about.gitlab.com/solutions/continuous-integration/)
* [Azure DevOps Pipelines](https://azure.microsoft.com/en-us/products/devops/pipelines)
* [Jenkins](https://www.jenkins.io/)
* [Circle CI](https://circleci.com/)

### Other related tools

For completeness, this section lists some other tools used by customers, but in general these tools are not recommended for use in deployment pipelines. A few notes are provided for each tool.

* [DataOps SOLE](https://docs.dataops.live/docs/sole/)
   * A declarative style database comparison tool
   * Part of the DataOps.live solution
   * SOLE stands for Snowflake Object Lifecycle Engine
   * SOLE is essentially a wrapper around the Snowflake Terraform Provider
   * Would recommend using if the customer is already using DataOps.live
* [SnowDDL](https://docs.snowddl.com/)
   * A declarative style database comparison tool
* [Titan Core](https://github.com/Titan-Systems/titan)
   * A declarative style database comparison tool
* [DLSync](https://github.com/Snowflake-Labs/dlsync)
   * A declarative style database comparison tool
* [Bytebase](https://www.bytebase.com/)
   * A declarative style database comparison tool
* [Alembic](https://alembic.sqlalchemy.org/)
   * An Object-Relational Mapping (ORM) tool written in Python
   * ORMs are used by application developers for managing the database state of their application
   * ORMs are not generally used by data engineers

## Important deployment tool considerations

The following section contains guidance around a few important tool considerations.

### Multiple tools to manage and deploy all objects

Real-world CI/CD deployment pipelines most often require multiple deployment tools (and steps) to deploy all of the possible Snowflake objects. Here are some of the most common situations involving multiple tools:

* Managing both "simple" and "complex" object types in Snowflake (the see [Simple vs Complex objects in Snowflake](#simple-vs-complex-objects-in-snowflake) for more details)
* Managing security and permissions if often done with a dedicated process, leveraging something like [Permifrost](https://gitlab.com/gitlab-data/permifrost) or a bespoke process.
* Tools that manage Snowflake objects directly, like `dbt` does with tables and views.

While it would be nice to have a single, imperative-style tool for deploying all Snowflake objects, the key point here is that customers should expect to leverage multiple tools in their CI/CD pipelines.

### Undo or Rollback

This is probably the most misunderstood feature offered by [Imperative database deployment tools](#imperative-database-deployment-tools). Two of the most popular imperative tools, Flyway and Liquibase, offer rollback or undo features. Most often the user has to create a separate undo or rollback script along with their deployment script. But in almost all real situation this feature does not work. For this feature to work the deployment has to either have been successful, or have failed in an almost trivial way, so that the rollback and easily undo what was done. In practice, however, deployments fail in a myriad of places and ways making it nearly impossible to roll back with out a super complicated script. And what's more if data has changed in a table since the deployment there is a further concern which must be addressed around what to do with the actual data.

So the recommendation is not to use undo or rollback features offered by some tools, but instead to always "roll forward" or "fail forward". The idea is really simple, when a deployment fails simply create a new versioned change that correctly handles the issue. It might be that the script finishes the previous failed deployment, or it might be that the script removes the objects created by the previous deployment. Either way it's a new, regular, "forward" migration.

## Snowflake native DCM features

While Snowflake has been working on native DCM features for a while, they are not ready for production use for customers. To make matters worse, there are many resources online that incorrectly encourage customers to build their own DCM like processes with Snowflake native features (primarily with `EXECUTE IMMEDIATE FROM`). For example, the main [Snowflake DevOps](https://docs.snowflake.com/en/developer-guide/builders/devops) landing page encourages exactly this approach. See [External resources to ignore](#external-resources-to-ignore) for additional related recommendations online that should be ignored.

The following are the native Snowflake features most commonly used for DCM:

* [CREATE OR ALTER](https://docs.snowflake.com/en/sql-reference/sql/create-or-alter)
   * A very useful feature in Snowflake to declaratively change the state of an individual object
   * Because `CREATE OR ALTER` modifies an object in place the object and its corresponding security grants, history and other associated objects are not lost
   * Can only make changes supported directly by the `ALTER` command (which is limited for many objects)
   * Can be extremely useful in an imperative deployment script
   * BUT this feature by itself is not a declarative DCM tool!
* [EXECUTE IMMEDIATE FROM](https://docs.snowflake.com/en/sql-reference/sql/execute-immediate-from)
   * Allows users to directly execute the contents of a SQL script that lives in a Snowflake Stage or Git Repository
   * Provides a way to execute a SQL script directly from a Snowflake warehouse, so the user doesn't need to set up and schedule a process on an external compute resource
   * Can be useful for many purposes but should not be used directly in deployment pipelines!
   * Is not a replacement for an imperative style DCM tool as it doesn't support change script types, logging, and much more
* [DCM PROJECTS](https://docs.snowflake.com/en/LIMITEDACCESS/dcm-projects/snowflake-dcm-projects)
   * A native declarative DCM tool for Snowflake
   * Still in Private Preview (PrPr) and only supports a small subset of Snowflake objects
   * Not recommended as a deployment tool for Snowflake
   * See the [More details on DCM Projects](#more-details-on-dcm-projects) section below for more details

### More details on DCM Projects

Snowflake's native DCM feature is named "DCM Projects". It is a declarative DCM feature that builds on `CREATE OR ALTER` with the new SQL DDL and DML commands `DEFINE` and `EXECUTE DCM PROJECT` (with PLAN and DEPLOY). For more details see [DCM PROJECTS](https://docs.snowflake.com/en/LIMITEDACCESS/dcm-projects/snowflake-dcm-projects).

While "DCM Projects" is positioned as a native DCM features, it is not recommended for most deployment scenarios. The most important reasons are:
* Snowflake DCM Projects implements a declarative style approach, which is inherently limited (see [Declarative database comparison tools](#declarative-database-comparison-tools) for more details)
* Snowflake DCM Projects are particularly limited, beyond the general declarative limitations
   * Only a small subset of objects are supported today (see [DCM PROJECTS](https://docs.snowflake.com/en/LIMITEDACCESS/dcm-projects/snowflake-dcm-projects) for the current list of supported objects)
   * Only changes supported the standard Snowflake `ALTER` command are supported (see the [ALTER TABLE ... ALTER COLUMN](https://docs.snowflake.com/en/sql-reference/sql/alter-table-column) for an example with `TABLE`s)
   * Object and column renaming is not supported
* Snowflake DCM Projects are still in an early preview stage, not Generally Available (GA)

But DCM Projects might be recommended in situations which meet the following requirements:
* Snowflake users who don't have a software or data engineering background
* Snowflake users who don't have access to a industry standard CI/CD tool (see [CI/CD tools](#cicd-tools) for a list of popular ones)
* Snowflake users who only need to manage objects supported by DCM Projects today (see [DCM PROJECTS](https://docs.snowflake.com/en/LIMITEDACCESS/dcm-projects/snowflake-dcm-projects) for the current list of supported objects)
* Snowflake users who don't make many changes directly to tables, leveraging tools like dbt instead for that

## Related Snowflake features

The following are a list of Snowflake features related to DCM. These are not direct DCM features, but they are often confused as such.

* [Snowflake Git Repository](https://docs.snowflake.com/en/developer-guide/git/git-overview)
   * Snowflake connecting to a customers Git repository does NOT mean DevOps
   * This feature is primarily to support stage 1 of the SDLC lifecycle (described in the [Defining the terms](#defining-the-terms) section)
   * This feature is often confused as being primarily use for deployment pipelines, which it is not
   * But this feature is very powerful, particularly when combined with Workspaces, the Snowflake web-based, modern IDE
* [Snowflake Python Management API](https://docs.snowflake.com/en/developer-guide/snowflake-python-api/reference/latest/index)
   * A helpful Python library for managing objects in Snowflake
   * Leverages the [Snowflake REST APIs](https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/snowflake-rest-api) for managing the lifecycle of many Snowflake objects
   * Does not currently support all objects in Snowflake
   * Can be useful for scripting and automating many administrative and operational tasks in Snowflake
   * Is not a replacement for a DCM tool
* [Snowflake Python Task Graph API](https://docs.snowflake.com/en/developer-guide/snowflake-python-api/snowflake-python-managing-tasks#managing-tasks-in-a-task-graph)
   * Builds on the [Snowflake Python Management API](https://docs.snowflake.com/en/developer-guide/snowflake-python-api/reference/latest/index) to manage Task DAGs
   * The Python library is modeled after the Airflow v1 Python spec
   * Can be used in a deployment pipeline to manage complex Task DAGs
   * The downside is that the object definitions and Python operational code are tightly coupled
   * But this is currently the best option for managing Python DAGs
* [Snowflake Python Connector](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector)
   * This is the core Python connector for Snowflake
   * Provides low-level connective and query execution capabilities for Snowflake
   * Used by every Python library or tool that connects to Snowflake
   * Not a replacement for a DCM tool

## External resources

### Helpful external resources

This recording is still the best overall guidance for automating deployments in Snowflake, despite being a few years old:
* [DevOps Recommendations when Building on Snowflake](https://snowflake.wistia.com/medias/khvlmhoy99)

These are useful for managing "complex" objects in Snowflake with the Snowflake CLI:
* [Data Engineering Pipelines with Snowpark Python](https://www.snowflake.com/en/developers/guides/data-engineering-pipelines-with-snowpark-python/)
  * Shows how to build Python data engineering pipelines in Snowflake Stored Procedures
  * Leverages VS Code and the VS Code extensions for Snowflake to develop the Python code
  * Leverages the Snowflake CLI tool in a GitHub Actions CI/CD pipeline to deploy the Snowflake `PROCEDURE` object
  * Provides the best practice for managing Python code that be run both directly while debugging and inside a stored procedure
  * Avoids having the user wrap the Python code in a `CREATE PROCEDURE` SQL block or use Python decorators to deploy the procedure
* [Getting Started with Data Engineering using Snowflake Notebooks](https://www.snowflake.com/en/developers/guides/data-engineering-with-notebooks/)
  * Shows how to build Python data engineering pipelines with Snowflake Notebooks
  * Leverages the Snowsight Workspaces experience to develop the standard `.ipynb` notebook and check into Git
  * Leverages the Snowflake CLI tool in a GitHub Actions CI/CD pipeline to deploy the complex Snowflake `NOTEBOOK PROJECT` object
  * Snowflake Notebooks v2 uses the complex `NOTEBOOK PROJECT` object in Snowflake
  * For comparison, Snowflake Notebooks v1 uses the complex `NOTEBOOK` object in Snowflake, which has been deprecated
* [Snowflake File-Based Entities (or FBEs)](https://medium.com/snowflake/snowflake-file-based-entities-or-fbes-f3c0225a6245)
   * File-Based Entities (or FBEs) are the primary way Snowflake implements "complex" objects in Snowflake
   * FBEs are objects in Snowflake that manage versioned sets of files related to an object 

These Quickstarts are useful for seeing how to use schemachange with a few of the popular CI/CD tools:
* [DevOps: Database Change Management with schemachange and Azure DevOps](https://www.snowflake.com/en/developers/guides/devops-dcm-schemachange-azure-devops/)
   * Uses schemachange for imperative database deployment (DCM) and Azure DevOps for CI/CD
* [DevOps: Database Change Management with schemachange and GitHub Actions](https://www.snowflake.com/en/developers/guides/devops-dcm-schemachange-github/)
   * Uses schemachange for imperative database deployment (DCM) and GitHub Actions for CI/CD
* [DevOps: Database Change Management with schemachange and Jenkins](https://www.snowflake.com/en/developers/guides/devops-dcm-schemachange-jenkins/)
   * Uses schemachange for imperative database deployment (DCM) and Jenkins for CI/CD

This Quickstart is useful for showing how to use Terraform with Snowflake
* [DevOps: Database Change Management with Terraform and GitHub Actions](https://www.snowflake.com/en/developers/guides/devops-dcm-terraform-github/)
   * Uses Terraform Cloud for state file management and GitHub Actions for CI/CD

These resources are helpful for working with Snowflake dbt Projects:
* [Productionize dbt Projects On Snowflake: A Practical Guide](https://www.youtube.com/watch?v=qC4e0nX4Hyw)
* [Snowflake dbt Projects Operations Deep Dive](https://medium.com/snowflake/snowflake-dbt-projects-operations-deep-dive-5b83a477fc86)

### Approaches to avoid

The most important set of resources to ignore are those which try and leverage Snowflake Git Repositories with `EXECUTE IMMEDIATE FROM` to run SQL deployment scripts directly from Snowflake. This approach was suggested in the past (see links below) but is not recommended as it doesn't use a standard imperative style DCM tool and runs the deployment process directly from Snowflake (see [Where should the deployment process run?](#where-should-the-deployment-process-run) for more details on why that's not recommended). Here are the key resources which suggest that approach, but please ignore any other ones suggesting a similar approach:

* [Snowflake DevOps](https://docs.snowflake.com/en/developer-guide/builders/devops)
* [Snowflake BUILD | The Future Of DevOps With Snowflake](https://www.youtube.com/watch?v=k20yLpW8-xU)
* [DevOps in Snowflake: How Git and Database Change Management enable a file-based object lifecycle](https://medium.com/snowflake/devops-in-snowflake-how-git-and-database-change-management-enable-a-file-based-object-lifecycle-1f61a0d5257c)
* [Accelerate Development and Productivity with DevOps in Snowflake](https://www.snowflake.com/en/blog/devops-snowflake-accelerating-development-productivity/)

These resources are outdated and should now be ignored:

* [Getting Started With Data Engineering Using Snowflake Notebooks](https://www.youtube.com/watch?v=1zdciOSf8mA)
   * The general approach here with Snowflake CLI to deploy the complex `NOTEBOOK` objects is correct, but the `NOTEBOOK` object has been superseded by the new `NOTEBOOK PROJECT` object
   * This content has been superseded by this updated Quickstart: [Data Engineering Pipelines with Snowpark Python](https://www.snowflake.com/en/developers/guides/data-engineering-pipelines-with-snowpark-python/)
