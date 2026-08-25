# Ticket: Add Members Organization Display Control

## Requirement Summary
When a user is staged to be added to a contract in the Add Members modal:
- If user is ALREADY a project member (via another contract in same project) → show their existing org as **read-only static text**
- If user is NEW to the project → show **editable dropdown** for org selection
- Prevent duplicate contract members (warning already exists)
- On Save, persist members with assigned organizations

---

## Files to Modify (6 total)

### 1. ParticipatingMemberService.java
**File:** `C:\CC4E\project-service\src\main\java\com\opentext\bim\projectservice\service\ParticipatingMemberService.java`

**Location:** At the end of the class, before the closing `}`

**Change Type:** ADD new method

**Code to Add:**
```java
public ParticipatingMember findProjectMemberByUserIdAndProjectId(UUID userId, UUID projectId) {
    return memberRepository.findByOtdsUserIdAndProjectId(userId, projectId);
}
```

**Context (last 3 lines of file before this):**
```java
    @Transactional
    public void deleteParticipatingMembers(UUID projectId) {
        memberRepository.deleteByProjectIdAndTenantIdAndSubscriptionId(projectId,
                UUID.fromString(authContextProvider.getTenantId()), UUID.fromString(authContextProvider.getSubscriptionId()));
    }
```

---

### 2. Query.java (GraphQL)
**File:** `C:\CC4E\project-service\src\main\java\com\opentext\bim\projectservice\graphql\Query.java`

**Location:** Before the last closing `}` of the class

**Change Type:** ADD new GraphQL query

**Code to Add:**
```java
@ExcludeMembershipCheck
@QueryMapping
public ParticipatingMember projectMemberStatus(@Argument String projectId, @Argument String userId) {
    try {
        return memberService.findProjectMemberByUserIdAndProjectId(
            UUID.fromString(userId), UUID.fromString(projectId));
    } catch (IllegalArgumentException e) {
        log.error("Invalid projectId or userId: {}", e.getMessage());
        return null;
    }
}
```

**Context (before this code):**
```java
    @ExcludeMembershipCheck
    @QueryMapping
    public List<UserPreferences> userPreferencesForAllUsers(@Argument String objectId, @Argument String objectType) {
        return userPreferencesService.getUserPreferencesForAllUsers(objectId, objectType);
    }
```

**Verify:** Check that `memberService` is already injected in the class (it should be).

---

### 3. member.service.ts
**File:** `C:\CC4E\xchange-ui\src\app\modules\shared\services\members\member.service.ts`

**Location:** After the `addMembers()` method, before `updateTableData()`

**Change Type:** ADD new service method

**Code to Add:**
```typescript
/** Check if a user is already a project member and return their existing organization */
checkProjectMembership(userId: string, projectId: string): Observable<any> {
    return this.gqlService
      .graphql(
        this.apiUrl.getSourceURL(URIKeyConstant.PROJECTS),
        `query projectMemberStatus($userId: String!, $projectId: String!) {
          projectMemberStatus(userId: $userId, projectId: $projectId) {
            id company { id name }
          }
        }`,
        { userId, projectId }
      )
      .pipe(map(data => (data['data'] ? data['data']['projectMemberStatus'] : null)));
}
```

**Context (method before it):**
```typescript
  addMembers(projectId: string, members: ParticipatingMember[]): Observable<any> {
    return this.gqlService
      .graphql(
        this.apiUrl.getSourceURL(URIKeyConstant.PROJECTS),
        // GraphQL mutation...
      );
}
```

---

### 4. add-members.component.ts
**File:** `C:\CC4E\xchange-ui\src\app\modules\members\add-members\add-members.component.ts`

#### 4a. Add two new properties
**Location:** After line ~72 (after `errorUser: any[]`)

**Change Type:** ADD properties

**Code to Add:**
```typescript
isExistingMemberInProject: boolean = false;
existingMemberOrganizationName: string = '';
```

**Context:**
```typescript
  currObs: Subscription[] = [];
  errorUser: any[];
```

#### 4b. Update `onUserSelect()` method
**Location:** Method starting at line ~243

**Change Type:** MODIFY the entire method

**Existing method signature:**
```typescript
onUserSelect(searchData) {
    // Clear previously selected users
    this.member.selectedUsers = [];

    // Check if searchData is an event or empty string, if so, return
    if (searchData instanceof Event || !searchData) {
      return;
    }

    // Ensure searchData is an array to handle multiple selections
    if (!Array.isArray(searchData)) {
      searchData = [searchData];
    }
    searchData.forEach(searchDataElement => {
      let user = this.member.users.find(user => user.key === searchDataElement);
      if (!user) {
        // If the user is not found in the users list, add it as a new user
        user = {
          key: searchDataElement,
          label: searchDataElement,
          subLabel: searchDataElement,
          graphic: {
            type: ItemGraphicType.LOGO,
            value: searchDataElement
          },
          isNotInList: true
        } as ItemSelectItem;
        this.member.users.push(user);
      }
      this.member.selectedUsers.push(user);
    });
  }
```

**Replace with:**
```typescript
onUserSelect(searchData) {
    this.member.selectedUsers = [];
    this.isExistingMemberInProject = false;
    this.existingMemberOrganizationName = '';

    if (searchData instanceof Event || !searchData) {
      return;
    }

    if (!Array.isArray(searchData)) {
      searchData = [searchData];
    }
    searchData.forEach(searchDataElement => {
      let user = this.member.users.find(user => user.key === searchDataElement);
      if (!user) {
        user = {
          key: searchDataElement,
          label: searchDataElement,
          subLabel: searchDataElement,
          graphic: { type: ItemGraphicType.LOGO, value: searchDataElement },
          isNotInList: true
        } as ItemSelectItem;
        this.member.users.push(user);
      }
      this.member.selectedUsers.push(user);
    });

    // For single-user selection, check if they are already a project member
    if (searchData.length === 1 && this.currProject?.id) {
      const ms = this.memberService.checkProjectMembership(searchData[0], this.currProject.id).subscribe(
        (result: any) => {
          if (result?.company?.name) {
            this.isExistingMemberInProject = true;
            this.existingMemberOrganizationName = result.company.name;
            this.selectedOrg = result.company.name;
          }
        },
        () => {}
      );
      this.currObs.push(ms);
    }
  }
```

---

### 5. add-members.component.html
**File:** `C:\CC4E\xchange-ui\src\app\modules\members\add-members\add-members.component.html`

#### 5a. Replace organization dropdown section
**Location:** Lines ~63-80 (the div with class `se-coordinator__body--drpdwn__organization`)

**Change Type:** REPLACE

**Existing code:**
```html
<div class="se-coordinator__body--drpdwn__organization">
  <label>
    {{ 'home.type.organization' | translate }}
    <span class="se-coordinator__body--drpdwn__organization--required-astrix">*</span>
  </label>
  <ot-item-select
    #organization
    [readOnly]="false"
    [placeholder]="organizationItemSelect.placeholder"
    [valid]="true"
    [options]="organizationItemSelect.options"
    (change)="onOrgChange($event)"
    [removeItem]="removeUserItem"
    [removeAllItems]="clearItem"
  ></ot-item-select>
</div>
```

**Replace with:**
```html
<div class="se-coordinator__body--drpdwn__organization">
  <label>
    {{ 'home.type.organization' | translate }}
    <span class="se-coordinator__body--drpdwn__organization--required-astrix">*</span>
  </label>
  <ng-container *ngIf="isExistingMemberInProject; else editableOrg">
    <span class="se-coordinator__body--drpdwn__organization--readonly">{{ existingMemberOrganizationName }}</span>
  </ng-container>
  <ng-template #editableOrg>
    <ot-item-select
      #organization
      [readOnly]="false"
      [placeholder]="organizationItemSelect.placeholder"
      [valid]="true"
      [options]="organizationItemSelect.options"
      (change)="onOrgChange($event)"
      [removeItem]="removeUserItem"
      [removeAllItems]="clearItem"
    ></ot-item-select>
  </ng-template>
</div>
```

#### 5b. Update "Add" button disabled condition
**Location:** Lines ~81-87 (the Add button's `[disabled]` attribute)

**Change Type:** MODIFY disabled condition

**Existing code:**
```html
<ot-flat-button
  [disabled]="
    users != undefined &&
    roles != undefined &&
    (users.selected.length <= 0 || roles.selected.length <= 0 || selectedOrg == undefined || selectedOrg == '')
  "
```

**Replace with:**
```html
<ot-flat-button
  [disabled]="
    users != undefined &&
    roles != undefined &&
    (users.selected.length <= 0 || roles.selected.length <= 0 || (!isExistingMemberInProject && (selectedOrg == undefined || selectedOrg == '')))
  "
```

---

## Build Validation Commands

### TypeScript/Angular build:
```bash
cd C:\CC4E\xchange-ui
npx ng build --configuration production
```
**Expected:** Clean build with no `error TS` messages (pre-existing SCSS deprecation warnings are OK)

### Java build (project-service):
```bash
cd C:\CC4E\project-service
.\gradlew.bat compileJava --no-daemon
```
**Expected:** No `error:` messages (pre-existing deprecation warnings about Hibernate are OK)

---

## Expected Behavior After Changes

### Scenario 1: User NEW to project
1. User types/selects a user email in Add Members modal
2. System queries `projectMemberStatus` GraphQL
3. Query returns `null` (user not in project)
4. `isExistingMemberInProject = false`
5. Organization dropdown shows as **editable dropdown** (normal behavior)
6. Add button requires both role AND org to be selected

### Scenario 2: User ALREADY in project (different contract)
1. User types/selects a user email in Add Members modal
2. System queries `projectMemberStatus` GraphQL
3. Query returns user's existing `company.name` (e.g., "Acme Corp")
4. `isExistingMemberInProject = true`, `existingMemberOrganizationName = "Acme Corp"`
5. Organization field shows as **read-only text: "Acme Corp"**
6. Add button is enabled (role selected + org auto-filled from existing membership)
7. On Save, user is added to new contract with their pre-assigned org

---

## Testing Checklist

- [ ] Frontend compiles without TypeScript errors
- [ ] Backend compiles without Java errors
- [ ] App starts without runtime errors
- [ ] Select a NEW user → org dropdown appears (editable)
- [ ] Select an EXISTING project member → org shows as read-only text
- [ ] Add button is disabled until role is selected (new user)
- [ ] Add button is enabled after role is selected (existing member, org auto-filled)
- [ ] Save persists members with correct organizations
- [ ] Duplicate member warning still appears

---

## Summary of Changes by File

| File | Lines Modified | Type | Strategy |
|------|---|---|---|
| ParticipatingMemberService.java | End of class | ADD method | INSERT_BEFORE_CLASS_END |
| Query.java | End of class | ADD GraphQL query | INSERT_BEFORE_CLASS_END |
| member.service.ts | After addMembers() | ADD service method | INSERT_AFTER_LINE |
| add-members.component.ts | ~72, ~243 | ADD properties + MODIFY method | INSERT_AFTER_IMPORTS + SEARCH/REPLACE |
| add-members.component.html | ~63-87 | REPLACE 2 blocks | SEARCH/REPLACE (2 blocks) |
| **TOTAL** | **6 changes across 5 files** | Mixed | **~150 lines of code** |

