import { Component, ViewEncapsulation, OnInit, AfterViewChecked, ElementRef, ViewChild,
	ChangeDetectorRef } from '@angular/core';
import { FormControl } from '@angular/forms';
import { ToastyService, ToastyConfig } from 'ng2-toasty';
import { Select2OptionData } from 'ng2-select2';
import { DatatableComponent } from '@swimlane/ngx-datatable';
import 'rxjs/add/operator/debounceTime';

import { ExecutionService } from './execution.service';
import { AuthService } from '../auth/auth.service';

import { WorkflowStatus } from '../models/execution/workflowStatus';
import { Workflow } from '../models/playbook/workflow';
import { ActionStatus } from '../models/execution/actionStatus';
import { Argument } from '../models/playbook/argument';
import { GenericObject } from '../models/genericObject';
import { CurrentAction } from '../models/execution/currentAction';

@Component({
	selector: 'execution-component',
	templateUrl: './execution.html',
	styleUrls: [
		'./execution.css',
	],
	encapsulation: ViewEncapsulation.None,
	providers: [ExecutionService, AuthService],
})
export class ExecutionComponent implements OnInit, AfterViewChecked {
	@ViewChild('actionStatusContainer') actionStatusContainer: ElementRef;
	@ViewChild('actionStatusTable') actionStatusTable: DatatableComponent;

	currentController: string;
	schedulerStatus: string;
	workflowStatuses: WorkflowStatus[] = [];
	displayWorkflowStatuses: WorkflowStatus[] = [];
	workflows: Workflow[] = [];
	availableWorkflows: Select2OptionData[] = [];
	workflowSelectConfig: Select2Options;
	selectedWorkflow: Workflow;
	loadedWorkflowStatus: WorkflowStatus;
	actionStatusComponentWidth: number;
	workflowStatusActions: GenericObject;

	filterQuery: FormControl = new FormControl();

	constructor(
		private executionService: ExecutionService, private authService: AuthService, private cdr: ChangeDetectorRef,
		private toastyService: ToastyService, private toastyConfig: ToastyConfig) {
	}

	ngOnInit(): void {
		this.toastyConfig.theme = 'bootstrap';

		this.workflowSelectConfig = {
			width: '100%',
			placeholder: 'Select a Workflow',
		};

		this.workflowStatusActions = {
			resume: 'resume',
			pause: 'pause',
			abort: 'abort',
		};

		this.getWorkflowNames();
		this.getWorkflowStatuses();
		this.getWorkflowStatusSSE();
		this.getActionResultSSE();

		this.filterQuery
			.valueChanges
			.debounceTime(500)
			.subscribe(event => this.filterWorkflowStatuses());
	}

	/**
	 * This angular function is used primarily to recalculate column widths for execution results table.
	 */
	ngAfterViewChecked(): void {
		// Check if the table size has changed, and recalculate.
		if (this.actionStatusTable && this.actionStatusTable.recalculate && 
			(this.actionStatusContainer.nativeElement.clientWidth !== this.actionStatusComponentWidth)) {
			this.actionStatusComponentWidth = this.actionStatusContainer.nativeElement.clientWidth;
			this.actionStatusTable.recalculate();
			this.cdr.detectChanges();
		}
	}

	/**
	 * Filters out the workflow statuses based on the value in the search/filter box.
	 * Checks against various parameters on the workflow statuses to set our display workflow statuses.
	 */
	filterWorkflowStatuses(): void {
		const searchFilter = this.filterQuery.value ? this.filterQuery.value.toLocaleLowerCase() : '';

		this.displayWorkflowStatuses = this.workflowStatuses.filter((s) => {
			return s.name.toLocaleLowerCase().includes(searchFilter) ||
				s.status.toLocaleLowerCase().includes(searchFilter) ||
				(s.current_action &&
					(s.current_action.name.toLocaleLowerCase().includes(searchFilter) ||
					s.current_action.action_name.toLocaleLowerCase().includes(searchFilter) ||
					s.current_action.app_name.toLocaleLowerCase().includes(searchFilter)));
		});
	}

	/**
	 * Gets a list of workflow statuses from the server for initial population.
	 */
	getWorkflowStatuses(): void {
		this.executionService
			.getWorkflowStatusList()
			.then(workflowStatuses => this.displayWorkflowStatuses = this.workflowStatuses = workflowStatuses)
			.catch(e => this.toastyService.error(`Error retrieving workflow statuses: ${e.message}`));
	}

	/**
	 * Initiates an EventSource for workflow statuses from the server.
	 * Updates existing workflow statuses for status updates or adds new ones to the list for display.
	 */
	getWorkflowStatusSSE(): void {
		this.authService.getAccessTokenRefreshed()
			.then(authToken => {
				const self = this;
				const eventSource = new (window as any)
					.EventSource(`api/streams/workflowqueue/workflow_status?access_token=${authToken}`);

				function eventHandler(message: any) {
					const workflowStatus: WorkflowStatus = JSON.parse(message.data);

					const matchingWorkflowStatus = self.workflowStatuses.find(ws => ws.execution_id === workflowStatus.execution_id);
					if (matchingWorkflowStatus) {
						Object.assign(matchingWorkflowStatus, workflowStatus);
					} else {
						self.workflowStatuses.push(workflowStatus);
						// Induce change detection by slicing array
						self.workflowStatuses = self.workflowStatuses.slice();
					}

					self.filterWorkflowStatuses();
				}
				eventSource.addEventListener('queued', eventHandler);
				eventSource.addEventListener('started', eventHandler);
				eventSource.addEventListener('paused', eventHandler);
				eventSource.addEventListener('resumed', eventHandler);
				eventSource.addEventListener('awaiting_data', eventHandler);
				eventSource.addEventListener('triggered', eventHandler);
				eventSource.addEventListener('aborted', eventHandler);

				eventSource.onerror = (err: Error) => {
					console.error(err);
				};
			});
	}

	/**
	 * Initiates an EventSource for action statuses from the server.
	 * Updates the parent workflow status' current_action if applicable.
	 * Will add/update action statuses for display if the parent workflow execution is 'loaded' in the modal.
	 */
	getActionResultSSE(): void {
		this.authService.getAccessTokenRefreshed()
			.then(authToken => {
				const self = this;
				const eventSource = new (window as any).EventSource(`api/streams/workflowqueue/actions?access_token=${authToken}`);

				function eventHandler(message: any) {
					const actionStatus: ActionStatus = JSON.parse(message.data);

					// if we have a matching workflow status, update the current app/action info.
					const matchingWorkflowStatus = self.workflowStatuses
						.find(ws => ws.execution_id === actionStatus.workflow_execution_id);
					if (matchingWorkflowStatus) {
						matchingWorkflowStatus.current_action = {
							execution_id: actionStatus.execution_id,
							id: actionStatus.action_id,
							name: actionStatus.name,
							app_name: actionStatus.app_name,
							action_name: actionStatus.action_name,
						};
					}

					// also add this to the modal if possible
					if (self.loadedWorkflowStatus && self.loadedWorkflowStatus.execution_id === actionStatus.workflow_execution_id) {
						const matchingActionStatus = self.loadedWorkflowStatus.action_statuses
							.find(r => r.execution_id === actionStatus.execution_id);

						if (matchingActionStatus) {
							Object.assign(matchingActionStatus, actionStatus);
						} else {
							self.loadedWorkflowStatus.action_statuses.push(actionStatus);
							// Induce change detection by slicing array
							self.loadedWorkflowStatus.action_statuses = self.loadedWorkflowStatus.action_statuses.slice();
						}
					}

					self.filterWorkflowStatuses();
				}

				eventSource.addEventListener('started', eventHandler);
				eventSource.addEventListener('success', eventHandler);
				eventSource.addEventListener('failure', eventHandler);

				eventSource.onerror = (err: Error) => {
					console.error(err);
				};
			});
	}

	/**
	 * Calls the workflow status endpoint to command a non-finished workflow to perform some action.
	 * @param workflowStatus WorkflowStatus to perform the action
	 * @param actionName Name of action to take (e.g. pause, resume, abort)
	 */
	performWorkflowStatusAction(workflowStatus: WorkflowStatus, actionName: string): void {
		this.executionService
			.performWorkflowStatusAction(workflowStatus.execution_id, actionName)
			.then(updatedWorkflowStatus => {
				Object.assign(workflowStatus, updatedWorkflowStatus);
				
				this.filterWorkflowStatuses();
			})
			.catch(e => this.toastyService.error(`Error performing ${actionName} on workflow: ${e.message}`));
	}

	/**
	 * Gets a list of playbooks and workflows from the server and compiles them into a list for selection.
	 */
	getWorkflowNames(): void {
		const self = this;

		this.executionService
			.getPlaybooks()
			.then(playbooks => {
				// Map all of the playbook's workflows and collapse them into a single top-level array.
				this.workflows = playbooks
					.map(pb => pb.workflows)
					.reduce((a, b) => a.concat(b), []);

				playbooks.forEach(playbook => {
					playbook.workflows.forEach(workflow => {
						self.availableWorkflows.push({
							id: workflow.id,
							text: `${playbook.name} - ${workflow.name}`,
						});
					});
				});
			});
	}

	/**
	 * Executes a given workflow. Uses the selected workflow (specified via the select2 box).
	 */
	excuteSelectedWorkflow(): void {
		this.executionService.addWorkflowToQueue(this.selectedWorkflow.id)
			.then(() => {
				this.toastyService.success(`Successfully started execution of "${this.selectedWorkflow.name}"`);
			})
			.catch(e => this.toastyService.error(`Error executing workflow: ${e.message}`));
	}

	/**
	 * Specifies the selected workflow from the select2. Used because in at least the version of select2 component we have,
	 * There is no two-way data binding available.
	 * @param event Event fired from the workflow select2 change.
	 */
	workflowSelectChange(event: any): void {
		if (!event.value || event.value === '') {
			this.selectedWorkflow = null;
		} else {
			this.selectedWorkflow = this.workflows.find(w => w.id === event.value);
		}
	}

	/**
	 * Opens a modal that contains the action results for a given workflow status.
	 * @param event JS Event from the hyperlink click
	 * @param workflowStatus Workflow Status to get action results for
	 */
	openActionStatusModal(event: Event, workflowStatus: WorkflowStatus): void {
		event.preventDefault();

		let actionResultsPromise: Promise<void>;
		if (this.loadedWorkflowStatus && this.loadedWorkflowStatus.execution_id === workflowStatus.execution_id) {
			actionResultsPromise = Promise.resolve();
		} else {
			actionResultsPromise = this.executionService.getWorkflowStatus(workflowStatus.execution_id)
				.then(fullWorkflowStatus => {
					this.loadedWorkflowStatus = fullWorkflowStatus;
				})
				.catch(e => this.toastyService
					.error(`Error loading action results for "${workflowStatus.name}": ${e.message}`));
		}

		actionResultsPromise.then(() => {
			($('.actionStatusModal') as any).modal('show');
		});
	}

	/**
	 * Converts an input object/value to a friendly string for display in the workflow status table.
	 * @param input Input object / value to convert
	 */
	getFriendlyJSON(input: any): string {
		if (!input) { return 'N/A'; }
		let out = JSON.stringify(input, null, 1);
		out = out.replace(/[\{\[\}\]"]/g, '').trim();
		if (!out) { return 'N/A'; }
		return out;
	}

	/**
	 * Converts an input argument array to a friendly string for display in the workflow status table.
	 * @param args Array of arguments to convert
	 */
	getFriendlyArguments(args: Argument[]): string {
		if (!args || !args.length) { return 'N/A'; }

		const obj: { [key: string]: string } = {};
		args.forEach(element => {
			if (element.value) { obj[element.name] = element.value; }
			if (element.reference) { obj[element.name] = element.reference.toString(); }
			if (element.selection && element.selection.length) {
				const selectionString = (element.selection as any[]).join('.');
				obj[element.name] = `${obj[element.name]} (${selectionString})`;
			}
		});

		let out = JSON.stringify(obj, null, 1);
		out = out.replace(/[\{\}"]/g, '');
		return out;
	}

	/**
	 * Gets the app name from a current action object or returns N/A if undefined.
	 * @param currentAction CurrentAction to use as input
	 */
	getAppName(currentAction: CurrentAction): string {
		if (!currentAction) { return'N/A'; }
		return currentAction.app_name;
	}

	/**
	 * Gets the action name from a current action object or returns N/A if undefined.
	 * @param currentAction CurrentAction to use as input
	 */
	getActionName(currentAction: CurrentAction): string {
		if (!currentAction) { return'N/A'; }
		let output = currentAction.name;
		if (output !== currentAction.action_name) { output = `${output} (${currentAction.action_name})`; }
		return output;
	}
}
