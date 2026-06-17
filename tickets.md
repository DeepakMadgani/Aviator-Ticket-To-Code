## Extracted Ticket Information

### Ticket 4157697
**Title:** QatarGas || "Prevent reopening completed deliverables in the project" option is enabled in the project but not applied for the contract in SE
**Description:**
Hi Team,

"Prevent reopening completed deliverables in the project is enabled" in the project but not applied to the SE contract
Please find the attached screenshot.

Regards,
Sunitha

TECHNICAL PROBLEM DESCRIPTION:
SOLUTION:
MODIFIED FILES: (full path & exact source version, per version)
AFFECTED PROGRAMS/SCRIPTS/LIBRARIES ETC:


### Ticket 4157705
**Title:** Core for Supplier Exchange - Make user email address case insensitive
**Description:**
Issue discovered during JIRA XCHANGE-11031 and CADMIN-12164 investigation.
As described by case contact:
"It seems that Caps is the key. if a user is registered ,ie, as SoontareeS@pttep.com and you apply API to SE with email soontarees@pttep.com the result will be 400. if you apply the API with SoontareeS@pttep.com, it works!
So it could be a potential bug that should be addressed @ server side, since in the world, the emails SoontareeS@pttep.com and soontarees@pttep.com are considered the same.
Probably at SE code level the check is performed in case sensitive, so SoontareeS@pttep.com and soontarees@pttep.com are different and for this reason SE makes a call to ETS."


### Ticket 4157769
**Title:** Description and Alternate name headings are not displayed properly in bulk upload validated template
**Description:**
Steps to reproduce :
1. Login to SE.
2. Create a project, contract, deliverable list, deliverable and upload a document.
3. Download bulk upload template and enter data.
4. Submit the deliverable.
5. Now upload bulk upload template.
6. An error message is displayed with a document link.
7. Download the document and verify "Description" and "Alternate Name" headers.


### Ticket 4157811
**Title:** Able to create items and get items by using expired service token
**Description:**
I generated a service token and made some post and get calls. After 20 mnts, I used same token to create project, contract. It is still working. I tried creating items again on next day and got 401 unauthorized error. I generated new token and these calls started working. When I replace the new token with old one and try hitting the calls, they are working again.

Ideally, token should be expired in 15 mnts.


### Ticket 4158510
**Title:** Error while downloading deliverable summary report
**Description:**
While downloading deliverable summary report, we have provision to select date range which doesn't exceed 180 days.
But seeing issue where in UI able to select date range for 181 days, butting get error while download.


### Ticket 4158525
**Title:** Acknowledgement button is not displayed for the transmittal when configured the acknowledgement option
**Description:**
Test step:
Login to the CCP application
Send the transmittal to the Xchange with acknowledgement
Login to the SE ui and verify the acknowledgement button for the transmittal
Observed result:Not able to see the Acknowledgment button

This issue is seen in the global ci environment
ACCEPTANCE CRITERIA:
Acknowledgement button should be displayed in the Xchange UI


### Ticket 4158546
**Title:** Contract members who are not reviewers can also see the review option for deliverables in pending acceptance state.
**Description:**
ACTUAL BEHAVIOR:

EXPECTED BEHAVIOR:

HOW-TO-REPRODUCE:


### Ticket 4158607
**Title:** Page is not refreshing after deleting the search content in library
**Description:**
Steps to reproduce:
1. Login to SE.
2. Create a project, contract.
3. Go to the library and upload some documents.
4. Search with any of the document names.
5. After getting the search results, delete all the search results.
6. Page is not getting updated.


### Ticket 4159432
**Title:** Download calls fails due to the 500 internal server error when tried to submit the deliverables with 0kb file size
**Description:**
Login to the CCP and create the document in the project which is registered with SE
In the SE goto the deliverables and upload the 0b file size and submit
Observed result:The deliverables is not able to download as the call are failing due to the 500 internal server error
{"@timestamp":"2023-07-17T02:42:20.252+0000","service":"CPX-CONNECTOR","@version":"23.4.0","default-log-level":"ERROR","class":"com.opentext.solutions.cpconnector.connectorservice.service.impl.CPPackageIngester","method":"importDocuments","message":"Error Occurred during ingestion : 500 Internal Server Error from GET https://contentservice-cpxqe.bp-paas.otxlab.net/content/cms%3Aotmay23se%23fd2af81d-bbde-43fa-8975-c0db710e1b4b/download/" ... }


### Ticket 4159514
**Title:** Supplier exchange application is very slow in all QE environments.
**Description:**
SE app is very slow and due to that many of the automation scripts are failing.
I mainly observe slowness issues during document upload, changing deliverables states.


### Ticket 4159515
**Title:** Xchange:Though the user has acknowledged the transmittal successful but the error message seen as 'you are not authorised...."
**Description:**
Test step:
Login to the xchange application
Select the transmittal and click on the acknowledge
Observed result:Erorr message is seen as you are not authorised but the transmittal is acknowledged successfully


### Ticket 4161950
**Title:** When manage from connector is enabled from project, allow creation checkbox is editable/enabled at contract level.
**Description:**
When manager from connector is enabled from project and mass update override is not enabled, allow creation checkbox is enabled at contract level.
Similar to other settings, allow creation should be editable at contract level only if manage from connector is set at contract level or mass update override is enabled. User shouldnt be able to edit a setting set at project level.
Also, when project settings are changed from enabled to set at contract level for manage from connector, manage from connector is enabled and the allow creation checkbox is editable but is unchecked at contract level.


### Ticket 4162805
**Title:** JATO -User not able to access any of the tabs on deliverables page UI on resizing the window
**Description:**
1. Login to Application
2. on the deliverable page UI with more number of deliverables and try resizing the window
Actual: Upon resizing the window, user is unable to access tabs- memebers, transmittals, library, Registers . No horizontal scroll bar to move right-left to access tabs.


### Ticket 4385169
**Title:** Return code is getting deleted on submitting the deliverable in grid view
**Description:**
HOW-TO-REPRODUCE:
1. login to SE Application V2
2. in the grid view, select the deliverable
3. Click on Submit
4. currently return code is Approved with Comments.
5. on submitting the deliverable , the return code filed is getting deleted.


### Ticket 4420401
**Title:** Library - unable to copy same file multiple times from clipboard
**Description:**
ACTUAL BEHAVIOR: Nitro QE: When user attempts to copy same file for the 5th time to the same location, then the copy operation fails
Note: in dev gcp and prod: copy operation fails for the 3rd time
EXPECTED BEHAVIOR: User should be able to copy files multiple times

HOW-TO-REPRODUCE:
1) Login to SE application
2) Navigate to a Project> Contract
3) Click on Library
4) Add a folder and upload a file
5) copy the file to clipboard and copy the file multiple times either on the library or by navigating inside the folder
6) When the third copy of the file is attempted, the copy operation fails
